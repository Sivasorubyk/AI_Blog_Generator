from django.shortcuts import render
from django.contrib.auth.models import User
from django.contrib.auth import authenticate, login, logout
from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.views.decorators.csrf import csrf_exempt
from django.http import JsonResponse
from django.conf import settings
import json
import yt_dlp
import os
import assemblyai as aai
import google.generativeai as genai
import time
import logging
import tempfile
from google.generativeai import GenerativeModel
import uuid
from pathlib import Path
from .models import BlogPost
from django.core.mail import send_mail
from django.utils.crypto import get_random_string

# Create your views here.
@login_required
def index(request):
    return render(request, 'index.html')

@csrf_exempt
def generate_blog(request):
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            yt_link = data['link']
        except (KeyError, json.JSONDecodeError):
            return JsonResponse({'error': 'Invalid data sent'}, status=400)

        try:
            # Start with getting the title
            title = yt_title(yt_link)
            
            # Return an initial response to keep the connection alive
            response = JsonResponse({
                'status': 'processing',
                'message': 'Starting download and transcription...',
                'title': title
            })
            response['X-Accel-Buffering'] = 'no'
            
            # Get transcript with progress updates
            try:
                transcription = get_transcription(yt_link)
                if not transcription:
                    return JsonResponse({'error': "Failed to get transcript"}, status=500)
            except Exception as e:
                logging.error(f"Transcription failed: {str(e)}")
                return JsonResponse({
                    'error': "Transcription failed. Please try again with a shorter video or contact support."
                }, status=500)

            # Generate blog content
            try:
                blog_content = generate_blog_from_transcription(transcription)
                if not blog_content:
                    return JsonResponse({'error': "Failed to generate blog article"}, status=500)
            except Exception as e:
                logging.error(f"Blog generation failed: {str(e)}")
                return JsonResponse({
                    'error': "Failed to generate blog content. Please try again."
                }, status=500)

            # Save blog article
            try:
                new_blog_article = BlogPost.objects.create(
                    user=request.user,
                    youtube_title=title,
                    youtube_link=yt_link,
                    generated_content=blog_content
                )
                new_blog_article.save()
            except Exception as e:
                logging.error(f"Failed to save blog: {str(e)}")
                return JsonResponse({
                    'error': "Failed to save blog article. Please try again."
                }, status=500)

            return JsonResponse({'content': blog_content, 'title': title})
            
        except Exception as e:
            logging.error(f"Error in generate_blog: {str(e)}")
            return JsonResponse({
                'error': "An unexpected error occurred. Please try again."
            }, status=500)
    else:
        return JsonResponse({'error': 'Invalid request method'}, status=405)

def yt_title(link):
    try:
        info = get_youtube_video(link)
        return info.get('title', 'Untitled Video')
    except Exception as e:
        logging.error(f"Error getting title: {str(e)}")
        return 'Untitled Video'

def get_temp_filepath():
    temp_dir = Path(settings.MEDIA_ROOT) / 'temp_audio'
    temp_dir.mkdir(parents=True, exist_ok=True)
    return temp_dir / f"{uuid.uuid4()}.mp3"

def download_audio(link):
    output_path = get_temp_filepath()
    
    ydl_opts = {
        'format': 'bestaudio/best',
        'ffmpeg_location': r"C:\Users\Admin\AppData\Local\Microsoft\WinGet\Links\ffmpeg.exe",
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }],
        'outtmpl': str(output_path.with_suffix('')),
        'quiet': True,
        # Add options for large file handling
        'buffersize': 1024 * 1024,  # 1MB buffer size
        'socket_timeout': 300,  # 5 minutes timeout
        'retries': 10,  # Number of retries
        'fragment_retries': 10,
        'continuedl': True,  # Continue partial downloads
    }
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([link])
            final_path = output_path.with_suffix('.mp3')
            if not final_path.exists():
                raise FileNotFoundError("Audio download failed")
            return str(final_path)
    except Exception as e:
        logging.error(f"Download error: {str(e)}")
        if output_path.exists():
            output_path.unlink(missing_ok=True)
        raise

def get_transcription(link):
    audio_path = None
    max_retries = 3

    try:
        audio_path = Path(download_audio(link))
        if not audio_path.exists():
            raise FileNotFoundError("Audio file not found")
            
        aai.settings.api_key = os.getenv("ASSEMBLYAI_API_KEY")
        transcriber = aai.Transcriber()

        # Enhanced configuration for better transcription
        config = aai.TranscriptionConfig(
            language_detection=True,
            punctuate=True,
            format_text=True,
            content_safety=False,
            webhook_url=None,
            speaker_labels=False,
            auto_chapters=True,  # Enable auto-chapters for better segmentation
            entity_detection=True  # Better entity recognition
        )

        for attempt in range(max_retries):
            try:
                transcript = transcriber.transcribe(
                    str(audio_path),
                    config=config
                )
                
                while transcript.status != 'completed':
                    if transcript.status == 'error':
                        raise Exception(f"Transcription failed: {transcript.error}")
                    time.sleep(5)
                    transcript = transcriber.get_transcript(transcript.id)

                # Verify the transcription is complete and ends with proper punctuation
                text = transcript.text.strip()
                if not text.endswith(('.', '!', '?')):
                    text += '.'
                
                return text

            except Exception as e:
                logging.error(f"Transcription attempt {attempt + 1} failed: {str(e)}")
                if attempt == max_retries - 1:
                    raise
                time.sleep(2 ** attempt)
        
    except Exception as e:
        logging.error(f"Transcription error: {str(e)}")
        raise
    finally:
        if audio_path and audio_path.exists():
            try:
                audio_path.unlink()
            except OSError:
                pass

def generate_blog_from_transcription(transcription):
    genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
    
    model = GenerativeModel('gemini-pro')
    
    # Adjusted generation config for more complete and coherent output
    generation_config = {
        "temperature": 0.7,
        "top_p": 0.8,
        "top_k": 40,
        "max_output_tokens": 8192  # Ensure we get a complete response
    }
    
    model.generation_config = generation_config
    
    prompt = f"""Based on the following transcript from a YouTube video, 
    write a comprehensive blog article. Format it using HTML tags as follows:
    
    1. Use <h1> tags for the main title
    2. Use <h2> tags for section headings
    3. Use <strong> tags for bold text (do not use markdown ** symbols)
    4. Wrap paragraphs in <p> tags
    5. Throughout the content:
       - Make important key points bold using <strong> tags
       - Emphasize significant statistics and numbers using <strong> tags
       - Highlight crucial phrases and main takeaways using <strong> tags
       - Make names, dates, and key concepts stand out using <strong> tags
    
    IMPORTANT: 
    - Ensure all sentences are complete
    - Cover all main points from the transcript
    - Each section should have a proper conclusion
    - The article should have a clear introduction and conclusion
    - Do not use markdown symbols like * or ** for formatting
    - Do not use # for headings
    - Use only HTML tags for all formatting
    - Start directly with the <h1> tag
    - End with a proper concluding paragraph
    
    Here's the transcript:
    
    {transcription}
    
    Generate the blog article with HTML formatting:"""
    
    try:
        response = model.generate_content(prompt)
        content = response.text.strip()
        
        # Clean up formatting
        content = content.replace('**', '')
        content = content.replace('*', '')
        content = content.replace('#', '')
        
        # Remove code block markers
        content = content.replace('```html', '').replace('```', '')
        content = content.replace("'''html", '').replace("'''", '')
        
        # Ensure content ends with proper HTML closure
        if not content.lower().endswith('</p>'):
            last_paragraph = content.split('</p>')[-1].strip()
            if last_paragraph:
                if not last_paragraph.endswith('.'):
                    last_paragraph += '.'
                content = content.rstrip() + '</p>'
        
        return content.strip()
    except Exception as e:
        logging.error(f"Error generating blog content: {str(e)}")
        raise

def blog_list(request):
    blog_articles = BlogPost.objects.filter(user=request.user)
    return render(request, 'all-blogs.html', {'blog_articles': blog_articles})

def blog_details(request, pk):
    blog_article_detail = BlogPost.objects.get(id=pk)
    if request.user == blog_article_detail.user:
        return render(request, 'blog-details.html', {'blog_article_detail': blog_article_detail})
    else:
        return redirect('/')

def user_login(request):
    if request.method == 'POST':
        email = request.POST['email']
        password = request.POST['password']
         
        try:
            user = User.objects.get(email=email)
            user = authenticate(request, username=user.username, password=password)
            if user is not None:
                login(request, user)
                return redirect('/')
            else:
                error_message = 'Invalid credentials'
                return render(request, 'login.html', {'error_message': error_message})
        except User.DoesNotExist:
            error_message = 'No account found with this email'
            return render(request, 'login.html', {'error_message': error_message})
        
    return render(request, 'login.html')

def user_signup(request):
    if request.method == 'POST':
        username = request.POST['username']
        email = request.POST['email']
        password = request.POST['password']
        repeat_password = request.POST['repeatPassword']
        
        if password == repeat_password:
            if User.objects.filter(email=email).exists():
                error_message = 'An account with this email already exists'
                return render(request, 'signup.html', {'error_message': error_message})
            
            try:
                user = User.objects.create_user(
                    username=username,
                    email=email,
                    password=password
                )
                user.save()
                login(request, user)
                return redirect('/')
            except Exception as e:
                error_message = 'Error creating account'
                return render(request, 'signup.html', {'error_message': error_message})
        else:
            error_message = 'Passwords do not match'
            return render(request, 'signup.html', {'error_message': error_message})
            
    return render(request, 'signup.html')

def user_logout(request):
    logout(request)
    return redirect('/')

def get_youtube_video(link, max_retries=3):
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'extract_flat': True,
    }
    
    for attempt in range(max_retries):
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(link, download=False)
                return info
        except Exception as e:
            logging.error(f"Attempt {attempt + 1}/{max_retries} failed: {str(e)}")
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
                continue
            raise

def forgot_password(request):
    if request.method == 'POST':
        email = request.POST.get('email')
        try:
            user = User.objects.get(email=email)
            # Generate token
            token = get_random_string(32)
            # Save token to user (you might want to create a separate model for this)
            user.profile.reset_token = token
            user.profile.save()
            
            # Create reset link
            reset_link = f"{request.scheme}://{request.get_host()}/reset-password/{token}"
            
            # Send email
            send_mail(
                'Reset Your Password',
                f'Click the following link to reset your password: {reset_link}',
                settings.DEFAULT_FROM_EMAIL,
                [email],
                fail_silently=False,
            )
            
            return render(request, 'forgot-password.html', {
                'success_message': 'Password reset instructions have been sent to your email.'
            })
            
        except User.DoesNotExist:
            return render(request, 'forgot-password.html', {
                'error_message': 'No account found with this email address.'
            })
            
    return render(request, 'forgot-password.html')

def reset_password(request, token):
    try:
        user = User.objects.get(profile__reset_token=token)
        
        if request.method == 'POST':
            password = request.POST.get('password')
            confirm_password = request.POST.get('confirm_password')
            
            if password != confirm_password:
                return render(request, 'reset-password.html', {
                    'error_message': 'Passwords do not match.'
                })
                
            user.set_password(password)
            user.profile.reset_token = None
            user.profile.save()
            user.save()
            
            return redirect('login')
            
        return render(request, 'reset-password.html')
        
    except User.DoesNotExist:
        return redirect('login')
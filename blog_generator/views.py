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

        # get yt title
        title = yt_title(yt_link)

        # get transcript
        transcription = get_transcription(yt_link)
        if not transcription:
            return JsonResponse({'error': "Failed to get transcript"}, status=500)

        # use OpenAI to generate the blog
        blog_content = generate_blog_from_transcription(transcription)
        if not blog_content:
            return JsonResponse({'error': "Failed to generate blog article"}, status=500)

        # save blog article to database
        new_blog_article = BlogPost.objects.create(
            user=request.user,
            youtube_title=title,
            youtube_link=yt_link,
            generated_content=blog_content
        )
        new_blog_article.save()

        # return blog article as a response
        return JsonResponse({'content': blog_content})
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
    try:
        audio_path = Path(download_audio(link))
        if not audio_path.exists():
            raise FileNotFoundError("Audio file not found")
            
        aai.settings.api_key = "429145d654184b7f83786ec239f4b099"
        transcriber = aai.Transcriber()
        transcript = transcriber.transcribe(str(audio_path))
        return transcript.text
        
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
    genai.configure(api_key="AIzaSyA2Rn_VSFSO0x7D3gVTaicM5xvckKvcxz4")
    
    model = GenerativeModel('gemini-pro')
    
    generation_config = {
        "temperature": 0.7,
        "max_output_tokens": 500,
        "top_p": 0.8,
        "top_k": 40
    }
    
    model.generation_config = generation_config
    
    prompt = f"""Based on the following transcript from a YouTube video, 
    write a comprehensive blog article, write it based on the transcript, 
    but dont make it look like a youtube video, make it look like a proper blog article:
    
    {transcription}
    
    Article:"""
    
    try:
        response = model.generate_content(prompt)
        return response.text
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
        username = request.POST['username']
        password = request.POST['password']
         
        user = authenticate(request, username=username, password=password)
        if user is not None:
            login(request, user)
            return redirect('/')
        else:
            error_message = 'Invalid credentials'
            return render(request, 'login.html', {'error_message': error_message})
        
    return render(request, 'login.html')


def user_signup(request):
    if request.method == 'POST':
        username = request.POST['username']
        email = request.POST['email']
        password = request.POST['password']
        repeat_password = request.POST['repeatPassword']
        if password == repeat_password:
            try:
                user = User.objects.create_user(username, email, password)
                user.save()
                login(request, user)
                return redirect('/')
            except:
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
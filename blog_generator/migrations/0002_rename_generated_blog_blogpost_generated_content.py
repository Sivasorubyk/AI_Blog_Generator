# Generated by Django 5.1.5 on 2025-02-17 05:20

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('blog_generator', '0001_initial'),
    ]

    operations = [
        migrations.RenameField(
            model_name='blogpost',
            old_name='generated_blog',
            new_name='generated_content',
        ),
    ]

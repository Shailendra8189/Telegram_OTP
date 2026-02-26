from django.shortcuts import render, redirect
from django.contrib.auth import login, logout
from django.utils import timezone
from .models import CustomUser, OTP, LoginHistory
import random
import string
import requests
import os

def send_telegram_otp(chat_id, msg):
    bot_token = os.getenv('TELEGRAM_BOT_TOKEN')
    if not bot_token:
        print("Error: TELEGRAM_BOT_TOKEN not found in env vars")
        return False
        
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": msg,
        "parse_mode": "Markdown"
    }
    try:
        response = requests.post(url, json=payload, timeout=10)
        response.raise_for_status()
        return True
    except requests.exceptions.RequestException as e:
        print(f"Telegram API Error: {e}")
        return False

def get_chat_id_from_updates(identifier):
    bot_token = os.getenv('TELEGRAM_BOT_TOKEN')
    if not bot_token:
        return None
        
    url = f"https://api.telegram.org/bot{bot_token}/getUpdates"
    try:
        response = requests.get(url, timeout=10)
        if response.status_code != 200:
            return None
        data = response.json()
        
        identifier_clean = identifier.replace('+', '').replace(' ', '').replace('-', '').lower()
        username_clean = identifier.lstrip('@').lower()
        
        for result in reversed(data.get('result', [])):
            message = result.get('message', {})
            chat = message.get('chat', {})
            from_user = message.get('from', {})
            contact = message.get('contact', {})
            
            chat_id = chat.get('id')
            if not chat_id:
                continue
                
            # Match by username
            if from_user.get('username') and from_user.get('username').lower() == username_clean:
                return chat_id
                
            # Match by phone number from contact
            if contact.get('phone_number'):
                phone = contact.get('phone_number').replace('+', '').replace(' ', '').replace('-', '')
                if phone == identifier_clean:
                    return chat_id
                    
            text = message.get('text', '')
            if text and text.replace('+', '').replace(' ', '').replace('-', '') == identifier_clean:
                return chat_id
                
    except Exception as e:
        print(f"Error fetching updates: {e}")
        
    return None

def index(request):
    if request.method == 'POST':
        action = request.POST.get('action')
        
        if action == 'logout':
            logout(request)
            return redirect('accounts:index')
            
        elif action == 'send_otp':
            identifier = request.POST.get('identifier', '').strip()
            if identifier:
                is_username = identifier.startswith('@') or not any(c.isdigit() for c in identifier)
                username_clean = identifier.lstrip('@') if is_username else None
                phone_clean = identifier if not is_username else None

                if is_username:
                    user = CustomUser.objects.filter(telegram_username=username_clean).first()
                else:
                    user = CustomUser.objects.filter(phone_number=phone_clean).first()

                # Get or fetch chat_id
                chat_id = user.telegram_chat_id if user and user.telegram_chat_id else None
                if not chat_id:
                    chat_id = get_chat_id_from_updates(identifier)

                if not chat_id:
                    request.session['state'] = 'send_otp'
                    request.session['error'] = 'Could not find your Telegram integration. Please send a message to the bot first.'
                    return redirect('accounts:index')
                    
                chat_id = str(chat_id)
                user_by_chat = CustomUser.objects.filter(telegram_chat_id=chat_id).first()
                
                if user_by_chat:
                    # If an orphaned incomplete user was created, delete it
                    if user and user != user_by_chat:
                        user.delete()
                        
                    # Update existing user with the new identifier
                    if is_username:
                        user_by_chat.telegram_username = username_clean
                    else:
                        user_by_chat.phone_number = phone_clean
                    user_by_chat.save()
                    user = user_by_chat
                else:
                    if user:
                        # User found but had no chat_id
                        user.telegram_chat_id = chat_id
                        user.save()
                    else:
                        # Create completely new user
                        if is_username:
                            user = CustomUser.objects.create(telegram_username=username_clean, telegram_chat_id=chat_id)
                        else:
                            user = CustomUser.objects.create(phone_number=phone_clean, telegram_chat_id=chat_id)
                
                # Check for active OTP (no spam on refresh)
                active_otp = OTP.objects.filter(
                    user=user, 
                    is_used=False,
                    expires_at__gt=timezone.now(),
                    attempts__lt=3
                ).first()
                
                if active_otp:
                    request.session['state'] = 'verify_otp'
                    request.session['identifier'] = identifier
                    request.session['otp_expires_at'] = active_otp.expires_at.timestamp()
                    request.session['otp_remaining_attempts'] = active_otp.max_attempts - active_otp.attempts
                    request.session['message'] = 'An active OTP has already been sent. Please wait for it to expire before requesting a new one.'
                    return redirect('accounts:index')
                
                # generate OTP
                code = ''.join(random.choices(string.digits, k=5))
                
                # invalidate old OTPs
                OTP.objects.filter(user=user, is_used=False).update(is_used=True)
                
                # Create OTP
                new_otp = OTP.objects.create(user=user, code=code)
                
                # Send Telegram Message
                success = send_telegram_otp(
                    chat_id, 
                    f"🔐 Your secure login OTP is: *{code}*\n\n_Do not share this code with anyone. It expires in 3 minutes._"
                )
                
                if not success:
                    # In test environment, maybe chat_id is invalid. 
                    print(f"Failed to send to {chat_id}, check if it's a valid Telegram chat ID.")
                
                request.session['state'] = 'verify_otp'
                request.session['identifier'] = identifier
                request.session['otp_expires_at'] = new_otp.expires_at.timestamp()
                request.session['otp_remaining_attempts'] = new_otp.max_attempts - new_otp.attempts
                request.session['message'] = 'OTP sent successfully via Telegram.' if success else 'Failed to send Telegram message. Please ensure you have sent a message to the bot.'
                return redirect('accounts:index')
            else:
                request.session['state'] = 'send_otp'
                request.session['error'] = 'Username or Phone Number is required.'
                return redirect('accounts:index')
                
        elif action == 'verify_otp':
            identifier = request.POST.get('identifier', '')
            code = request.POST.get('code')
            
            try:
                is_username = identifier.startswith('@') or not any(c.isdigit() for c in identifier)
                if is_username:
                    user = CustomUser.objects.get(telegram_username=identifier.lstrip('@'))
                else:
                    user = CustomUser.objects.get(phone_number=identifier)
            except CustomUser.DoesNotExist:
                request.session['state'] = 'send_otp'
                request.session['error'] = 'User not found. Please try again.'
                return redirect('accounts:index')
                
            # Check for generic OTPs before verifying specific one
            active_otp = OTP.objects.filter(user=user, is_used=False).order_by('-created_at').first()
            
            if active_otp:
                # If they tried, increment attempt
                active_otp.attempts += 1
                active_otp.save()
                
                if not active_otp.is_valid:
                    active_otp.is_used = True
                    active_otp.save()
                    request.session['state'] = 'send_otp'
                    request.session['error'] = 'Too many attempts or OTP expired. Please request a new one.'
                    return redirect('accounts:index')
            
            # Now verify the exact code match
            otp_obj = OTP.objects.filter(user=user, code=code, is_used=False).first()
            
            x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
            if x_forwarded_for:
                ip = x_forwarded_for.split(',')[0]
            else:
                ip = request.META.get('REMOTE_ADDR')
                
            user_agent = request.META.get('HTTP_USER_AGENT', '')
            
            if otp_obj and otp_obj.is_valid:
                otp_obj.is_used = True
                otp_obj.save()
                
                # login user
                login(request, user, backend='django.contrib.auth.backends.ModelBackend')
                
                # log history
                LoginHistory.objects.create(
                    user=user, 
                    ip_address=ip, 
                    user_agent=user_agent, 
                    status='SUCCESS'
                )
                
                return redirect('accounts:index')
            else:
                LoginHistory.objects.create(
                    user=user, 
                    ip_address=ip, 
                    user_agent=user_agent, 
                    status='FAILED'
                )
                request.session['state'] = 'verify_otp'
                request.session['identifier'] = identifier
                request.session['otp_expires_at'] = active_otp.expires_at.timestamp() if active_otp else None
                request.session['otp_remaining_attempts'] = active_otp.max_attempts - active_otp.attempts if active_otp else 0
                request.session['error'] = 'Invalid or expired OTP.'
                return redirect('accounts:index')

    # GET Request
    if request.user.is_authenticated:
        recent_logins = LoginHistory.objects.filter(user=request.user).order_by('-login_time')[:10]
        return render(request, 'accounts/index.html', {
            'recent_logins': recent_logins
        })
    else:
        state = request.session.pop('state', 'send_otp')
        message = request.session.pop('message', None)
        error = request.session.pop('error', None)
        identifier = request.session.pop('identifier', None)
        otp_expires_at = request.session.pop('otp_expires_at', None)
        otp_remaining_attempts = request.session.pop('otp_remaining_attempts', None)
        
        return render(request, 'accounts/index.html', {
            'state': state,
            'message': message,
            'error': error,
            'identifier': identifier,
            'otp_expires_at': otp_expires_at,
            'otp_remaining_attempts': otp_remaining_attempts
        })

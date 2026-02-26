from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils import timezone
import datetime


# Custom User Model

class CustomUser(AbstractUser):
    # Remove default username field
    username = None  

    phone_number = models.CharField(
        max_length=15,
        unique=True,
        null=True,
        blank=True
    )

    telegram_username = models.CharField(
        max_length=50,
        unique=True,
        null=True,
        blank=True
    )

    telegram_chat_id = models.CharField(
        max_length=50,
        unique=True,
        null=True,
        blank=True
    )

    # Choose your primary login field
    USERNAME_FIELD = 'telegram_username'
    REQUIRED_FIELDS = []  # No extra required fields

    def __str__(self):
        return self.telegram_username or self.phone_number or "User"


# OTP Model

class OTP(models.Model):
    user = models.ForeignKey(
        CustomUser,
        on_delete=models.CASCADE,
        related_name='otps'
    )

    code = models.CharField(max_length=5)

    created_at = models.DateTimeField(auto_now_add=True)

    expires_at = models.DateTimeField(
        null=True,
        blank=True
    )

    is_used = models.BooleanField(default=False)
    
    attempts = models.IntegerField(default=0)
    max_attempts = models.IntegerField(default=3)

    class Meta:
        ordering = ['-created_at']

    def save(self, *args, **kwargs):
        if not self.expires_at:
            self.expires_at = timezone.now() + datetime.timedelta(minutes=3)
        super().save(*args, **kwargs)

    @property
    def is_valid(self):
        return (
            not self.is_used and
            self.attempts < self.max_attempts and
            self.expires_at and
            timezone.now() <= self.expires_at
        )

    def __str__(self):
        return f"OTP for {self.user}"


# Login History Model

class LoginHistory(models.Model):
    STATUS_CHOICES = (
        ('SUCCESS', 'Success'),
        ('FAILED', 'Failed'),
    )

    user = models.ForeignKey(
        CustomUser,
        on_delete=models.CASCADE,
        related_name='login_history'
    )

    login_time = models.DateTimeField(auto_now_add=True)

    ip_address = models.GenericIPAddressField(
        null=True,
        blank=True
    )

    user_agent = models.CharField(
        max_length=255,
        null=True,
        blank=True
    )

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES
    )

    class Meta:
        ordering = ['-login_time']

    def __str__(self):
        return f"{self.user} - {self.status}"
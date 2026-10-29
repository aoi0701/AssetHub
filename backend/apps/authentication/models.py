from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    class Role(models.TextChoices):
        ADMIN = 'ADMIN', 'Admin'
        MANAGER = 'MANAGER', 'Manager'
        USER = 'USER', 'User'

    role = models.CharField(
        max_length=20,
        choices=Role.choices,
        default=Role.USER,
        help_text='User role in system RBAC',
    )
    full_name = models.CharField(
        max_length=150,
        blank=True,
        default='',
        help_text='Full name of the user',
    )
    phone = models.CharField(
        max_length=20,
        blank=True,
        default='',
        help_text='Contact phone number',
    )
    department = models.ForeignKey(
        'departments.Department',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='users',
        help_text='Associated department',
    )
    auth_version = models.IntegerField(
        default=1,
        help_text='Version counter incremented to invalidate existing JWT tokens',
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        help_text='Timestamp when user was created',
    )

    class Meta:
        ordering = ['-date_joined']
        verbose_name = 'User'
        verbose_name_plural = 'Users'

    def __str__(self):
        return f"{self.username} ({self.role})"

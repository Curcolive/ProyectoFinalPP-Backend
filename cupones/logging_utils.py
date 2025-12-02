from django.contrib.auth import get_user_model
from .models import SystemLog

User = get_user_model()


def create_log(user, action: str, detail: str = ""):
    if isinstance(user, User) and not user.is_authenticated:
        user = None

    SystemLog.objects.create(
        user=user,
        action=action,
        detail=detail or "",
    )

def log_action(user, action, detail=""):
    if user and not user.is_authenticated:
        user = None

    SystemLog.objects.create(
        user=user,
        action=action,
        detail=detail
    )

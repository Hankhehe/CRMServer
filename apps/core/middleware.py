import time
from django.contrib.auth import logout
from django.shortcuts import redirect
from django.urls import reverse
from .models import SystemSetting
from .permissions import log_action


class SessionIdleTimeoutMiddleware:
    """
    Middleware that enforces session idle timeout based on SystemSetting.idle_timeout_minutes.
    - If user is inactive for more than idle_timeout_minutes, logs them out and redirects to login.
    - If user closes browser and re-opens after the timeout period, their session is invalidated.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated:
            # Determine configured idle timeout in minutes
            try:
                system_settings = SystemSetting.get_settings()
                timeout_minutes = system_settings.idle_timeout_minutes or 10
            except Exception:
                timeout_minutes = 10

            timeout_seconds = timeout_minutes * 60
            now = time.time()
            last_activity = request.session.get("last_activity")

            if last_activity is not None:
                elapsed = now - last_activity
                if elapsed > timeout_seconds:
                    user = request.user
                    try:
                        log_action(
                            user=user,
                            action="使用者登出",
                            target_type="Auth",
                            target_identifier=user.username,
                            ip_address=self._client_ip(request),
                        )
                    except Exception:
                        pass

                    logout(request)
                    return redirect(reverse("login"))

            # Refresh last activity and session expiry
            request.session["last_activity"] = now
            request.session.set_expiry(timeout_seconds)

        response = self.get_response(request)
        return response

    @staticmethod
    def _client_ip(request):
        f = request.META.get("HTTP_X_FORWARDED_FOR")
        return f.split(",")[0].strip() if f else request.META.get("REMOTE_ADDR")

from django.contrib import admin
from django.urls import path, include
from rest_framework_simplejwt.views import TokenRefreshView
from cupones.views import MyTokenObtainPairView, SignupView, PasswordResetRequestView, PasswordResetConfirmView, GoogleLoginView, CompleteProfileView

urlpatterns = [
    path('admin/', admin.site.urls),
    path('cupones/', include('cupones.urls')),
    path('api/token/', MyTokenObtainPairView.as_view(), name='token_obtain_pair'),
    path('api/token/refresh/', TokenRefreshView.as_view(), name='token_refresh'),
    path("signup/", SignupView.as_view(), name="signup"),
    path("password-reset/request/", PasswordResetRequestView.as_view(), name="password_reset_request"),
    path("password-reset/confirm/", PasswordResetConfirmView.as_view(), name="password_reset_confirm"),
    path("google-login/", GoogleLoginView.as_view(), name="google_login"),
    path("complete-profile/", CompleteProfileView.as_view()),
]
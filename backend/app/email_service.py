"""Resend email integration. Install: pip install resend"""
import os
import resend

resend.api_key = os.getenv("RESEND_API_KEY", "")
FROM_ADDRESS   = os.getenv("EMAIL_FROM",     "noreply@yourdomain.com")
APP_URL        = os.getenv("APP_URL",        "http://localhost:8000")


def send_password_reset_email(to_email: str, reset_token: str) -> None:
    """Raises on failure — caller should handle exceptions."""
    reset_url = f"{APP_URL}/reset-password?token={reset_token}"
    resend.Emails.send({
        "from":    FROM_ADDRESS,
        "to":      [to_email],
        "subject": "Reset your password",
        "html": f"""
            <h2>Password Reset</h2>
            <p>You requested a password reset for your account.</p>
            <p><a href="{reset_url}" style="background:#3b82f6;color:white;padding:12px 24px;
               border-radius:6px;text-decoration:none;">Reset Password</a></p>
            <p>This link expires in 1 hour.</p>
            <p>If you didn't request this, you can safely ignore this email.</p>
        """,
    })
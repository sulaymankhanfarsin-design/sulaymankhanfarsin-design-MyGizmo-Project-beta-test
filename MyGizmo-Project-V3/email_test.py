import smtplib
import os
from dotenv import load_dotenv

# .env ফাইল লোড করুন
load_dotenv()

EMAIL_ADDRESS = os.getenv('MAIL_USERNAME')
EMAIL_PASSWORD = os.getenv('MAIL_PASSWORD')

print(f"Testing Email Configuration...")
print(f"User: {EMAIL_ADDRESS}")
print(f"Password Length: {len(EMAIL_PASSWORD) if EMAIL_PASSWORD else 0}")

try:
    # Gmail সার্ভারের সাথে কানেক্ট করার চেষ্টা
    print("1. Connecting to Gmail server (smtp.googlemail.com:587)...")
    server = smtplib.SMTP('smtp.googlemail.com', 587)
    server.starttls()
    
    # লগইন করার চেষ্টা
    print("2. Attempting to Login...")
    server.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
    print("✅ Login Successful!")
    
    # ইমেইল পাঠানোর চেষ্টা
    print("3. Sending Test Email...")
    subject = "Test Email from MyGizmo"
    body = "If you reading this, your email configuration is perfect!"
    msg = f"Subject: {subject}\n\n{body}"
    
    server.sendmail(EMAIL_ADDRESS, EMAIL_ADDRESS, msg)
    print("✅ Email Sent Successfully!")
    
    server.quit()
    
except Exception as e:
    print("\n❌ ERROR HAPPENED:")
    print(e)
    print("\nPotential Fixes:")
    print("- If error is 'Username and Password not accepted': Check your App Password again.")
    print("- If error is related to 'spaces': Ensure no spaces in .env password.")
    print("- If error is 'ConnectionRefused': Your internet or firewall might be blocking port 587.")
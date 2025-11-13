import os
from django.contrib.auth.models import User

email = os.environ.get('DJANGO_SUPERUSER_EMAIL')
if User.objects.filter(email=email).exists():
    print("EXISTS")

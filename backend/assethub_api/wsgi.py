"""
WSGI config for assethub_api project.
"""

import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'assethub_api.settings')

application = get_wsgi_application()

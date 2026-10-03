from django.shortcuts import render

from learning_apps.accounts.views import landing_auth_context
from .student_access import redirect_authenticated_user


def index(request):
    if request.user.is_authenticated:
        return redirect_authenticated_user(request)
    return render(request, "index.html", landing_auth_context())


def about(request):
    return index(request)

from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.views import TokenObtainPairView


class LoginView(TokenObtainPairView):
    throttle_scope = "login"


class MeView(APIView):
    def get(self, request):
        u = request.user
        return Response(
            {
                "id": u.id,
                "username": u.username,
                "role": "admin" if u.is_superuser else u.role,
            }
        )

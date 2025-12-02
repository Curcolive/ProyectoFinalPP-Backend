from rest_framework.generics import ListAPIView
from rest_framework.permissions import IsAdminUser
from .models import SystemLog
from .serializers import SystemLogSerializer
from rest_framework.filters import OrderingFilter
from rest_framework.pagination import PageNumberPagination
from django_filters.rest_framework import DjangoFilterBackend


class LogsPagination(PageNumberPagination):
    page_size = 30
    page_size_query_param = 'page_size'
    max_page_size = 200


class SystemLogListAPI(ListAPIView):
    permission_classes = [IsAdminUser] 
    serializer_class = SystemLogSerializer
    queryset = SystemLog.objects.all()
    pagination_class = LogsPagination
    filter_backends = [DjangoFilterBackend, OrderingFilter]
    filterset_fields = ['user', 'action']
    ordering_fields = ['timestamp']
    ordering = ['-timestamp']

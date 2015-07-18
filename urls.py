from django.conf.urls import include, url
from . import views
from .views import *

urlpatterns = [
    url(r'^v1/get-auth-token/$', bw_obtain_auth_token),
    url(r'^v1/get-profile/$', bw_rest_api_get_profile),
    url(r'^v1/get-voting-problem/$', bw_rest_api_get_voting_problem),
    url(r'^v1/poll-answer/(?P<slug>\S+)/$', bw_rest_api_submit_poll_answer),
    url(r'^v1/get-recent-answers/$', bw_rest_api_get_recent_answers),
    url(r'^v1/get-recent-published/$', bw_rest_api_get_recent_published),
    url(r'^v1/create-problem/$', bw_rest_api_create_new_problem),
    url(r'^v1/get-problem/(?P<slug>\S+)/$', bw_rest_api_get_problem),
    url(r'^v1/get-responses/(?P<slug>\S+)/$', bw_rest_api_get_responses),

    #Debug only
    url(r'^v1/unpublish-problem/(?P<slug>\S+)/$', bw_rest_api_unpublish_problem),
    url(r'^v1/update-count/(?P<slug>\S+)/$', bw_rest_api_update_count),
]

from django.contrib.auth.models import User
from django.contrib.auth import authenticate
from django.utils.translation import ugettext_lazy as _
from rest_framework import exceptions, serializers

class BWAuthTokenSerializer(serializers.Serializer):
    username = serializers.CharField()
    password = serializers.CharField(style={'input_type': 'password'})

    def validate(self, attrs):
        username = attrs.get('username')
        password = attrs.get('password')
        msg = ''
        error = False
        if username and password:
            user = authenticate(username=username, password=password)
            if user:
		if user.profile.banned:
			msg = _('User account has been banned from BridgeWinners.')
			error = True
		elif not user.is_active:
			msg = _('User account has not yet been activated.  Please click the link in the activation mail that you received when you created the account.  If you did not receive this email, please email support@bridgewinners.com.')
			error = True
            else:
		try:
			user = User.objects.get(username=username)
			msg = _('Unable to log in with provided credentials. The password you typed did not match the one we have on file. Please try again; remember that passwords are case-sensitive so capital letters matter.')
			error = True
		except User.DoesNotExist:
			msg = _('There is no user on file with that username. Please try again; remember that usernames are case-sensitive so capital letters matter.')
			error = True
        else:
            msg = _('Must include "username" and "password".')
            error = True
            
        attrs['msg'] = msg
        attrs['error'] = error
        attrs['user'] = user
        return attrs


from rest_framework.views import APIView
from rest_framework import parsers
from rest_framework import renderers
from rest_framework.response import Response
from rest_framework.authtoken.models import Token
from rest_framework import authentication, permissions
from .serializers import BWAuthTokenSerializer
from rest_framework import status
from bw.thumbnails import bw_get_thumbnail
from bw_poll.models import *
from bw_poll.forms import *
from django.views.generic import *
from bw.inlines import *
from bw_multiplechoice import question_answers
from django.contrib.auth.decorators import login_required
from bw.helpers import get_object_or_none, percent_esc, sample_cdf, compute_cdf
from bw.exceptions import *
from bw_article.article_parts import parts
from bw_poll.signals import vote_cast
from bw_follow.models import *
from bw_apps.support import get_current_app
from django.views.decorators.csrf import csrf_protect
from bw.models import Profile
from bw.forms import *
from bw.sitewidesettings import *
from bw_poll.views import get_strategy_problems
from django.template.loader import render_to_string

class BWObtainAuthToken(APIView):
    throttle_classes = ()
    permission_classes = ()
    parser_classes = (parsers.FormParser, parsers.MultiPartParser, parsers.JSONParser,)
    renderer_classes = (renderers.JSONRenderer,)
    serializer_class = BWAuthTokenSerializer	

    def post(self, request):
	serializer = self.serializer_class(data=request.data)
	serializer.is_valid(raise_exception=True)
	user = serializer.validated_data['user']
	msg = serializer.validated_data['msg']
	error = serializer.validated_data['error']
	tokenKey = ''
	name = ''
	avatar = ''
	if not error:
	    token, created = Token.objects.get_or_create(user=user)
	    tokenKey = token.key
	    name = user.first_name + ' ' + user.last_name
	    avatar = bw_get_thumbnail( user.profile.get_avatar(), '21x21').url
	return Response( {'error': error, 'token': tokenKey, 'message': msg, 'name': name, 'avatar': avatar} )

bw_obtain_auth_token = BWObtainAuthToken.as_view()  

class GetProfile(APIView):
    """
    Get profile of authenticated user.

    * Requires token authentication.
    * Only authenticated are able to access this view.
    """
    authentication_classes = (authentication.TokenAuthentication,)
    permission_classes = (permissions.IsAuthenticated,)

    def get(self, request, format=None):
        """
        Return profile of user
        """
        user = request.user
        statusCode = status.HTTP_200_OK
        name = user.first_name + ' ' + user.last_name
        avatar = bw_get_thumbnail( user.profile.get_avatar(), '21x21').url
        return Response({'username': user.username, 'name': name, 'avatar': avatar}, statusCode)

bw_rest_api_get_profile = GetProfile.as_view()

class GetVotingProblem(APIView):
    """
    Get next voting problem of authenticated user.

    * Requires token authentication.
    * Only authenticated are able to access this view.
    """
    authentication_classes = (authentication.TokenAuthentication,)
    permission_classes = (permissions.IsAuthenticated,)

    def post(self, request, format=None):
        """
        Return next voting problem of user
        """
        try:
            exclude_slug = request.POST.get( 'exclude', None );
            
            all_problems = Poll.objects.exclude( poll_type='Basic' )
            all_problems = all_problems.exclude( article__unpublished=True )
            all_problems = all_problems.exclude( article__deleted=True )
            all_problems = all_problems.filter( article__author__profile__verification_status=Profile.VERIFICATION_STATUS_VERIFIED )
            
            if request.user.is_authenticated():
                my_answers = [a for a in PollResponse.objects.filter(user_id=request.user.id).exclude( poll__poll_type='Basic' ).values_list('poll_id',flat=True)]
                my_ignores = [i for i in Ignore.objects.filter( user_id=request.user.id, articles=True ).values_list( 'ignored_user', flat=True )]
                available_problems = all_problems.exclude( id__in=my_answers )
                available_problems = available_problems.exclude( article__author_id__in=my_ignores )
            else:
                available_problems = all_problems
                    
            if exclude_slug:
                available_problems = available_problems.exclude( article__slug=exclude_slug )
                            
            site_settings = SitewideSettings.objects.get_singleton(request)
                    
            sample_strategy_names = [
                "Sampling from all problems",
                "Written by %d most popular people" % site_settings.num_popular_people,
                "Written by people I'm following"
            ]
            pdf = [site_settings.frontpage_problem_everyone_weight, site_settings.frontpage_problem_popular_weight]
            if request.user.is_authenticated():
                pdf.append(site_settings.frontpage_problem_following_weight)
                    
            cdf = compute_cdf( pdf )
            sample_strategy = sample_cdf( cdf )
            initial_sample_strategy_name = sample_strategy_names[sample_strategy]

            while True:
                strategy_problems = get_strategy_problems( request.user, available_problems, site_settings.num_popular_people, sample_strategy )
                if strategy_problems.exists() or sample_strategy == 0:
                    break;
                sample_strategy -= 1
                    
            sample_strategy_name = sample_strategy_names[sample_strategy]
            sample_likelihood = 0
            if sample_strategy == 0:
                sample_likelihood = cdf[0]
            elif sample_strategy > 0:
                sample_likelihood = cdf[sample_strategy] - cdf[sample_strategy-1]
                    
            num_problems = strategy_problems.count()
            if num_problems > 0:
                import random, math
                uniform = random.random()
                exponential = - math.log(uniform) / 15 # fudge factor from experiments
                idx = min( int( exponential * num_problems ), num_problems-1 )
                problem = strategy_problems.order_by('-article__publish_date')[idx]
                user = problem.article.author
                name = user.first_name + ' ' + user.last_name
                avatar = bw_get_thumbnail( user.profile.get_avatar(), '21x21').url
                content = render_to_string('bw_restapi/problem_description.html', { 'problem': problem })
                return Response({"error": False, 'alldone': False, 'slug': problem.article.slug, 'description': content, 'type': problem.poll_type, 'lin_str': problem.hand.lin_str(), 'author': name, 'avatar': avatar, 'scoring': problem.scoring, 'vulnerability': problem.vul, 'auction': problem.auction, 'dealer': problem.dealer})
            else:
                return Response({"error": False, 'alldone': True})
        except Exception as e:
            return Response( { "error": True, "message": e.message } )            

bw_rest_api_get_voting_problem = GetVotingProblem.as_view()

class SubmitPollAnswer(APIView):
    """
    Submit poll answer

    * Requires token authentication.
    * Only authenticated are able to access this view.
    """
    authentication_classes = (authentication.TokenAuthentication,)
    permission_classes = (permissions.IsAuthenticated,)

    def post(self, request, slug, format=None):
        """
        Submit poll answer
        """
        try:
            poll = get_object_or_none( Poll, article__slug=slug )
            if not poll:
                return Response( { "error": True, "message": "No such Poll" } )
            poll.article.update_cache()
            decr_answer = None
            incr_answer = None
            decr_abstain = False

            # see if the user has already answered, and change the counts.
            new_vote = False
            response = get_object_or_none( PollResponse, user=request.user, poll=poll )
            if not response:
                new_vote = True
                response = PollResponse( user=request.user, poll=poll )
            else:
                if not poll.allow_answer_changes:
                    raise PollAnswerChangeException( "You're not allowed to change your answer to this poll." )
                if response.answer != None:
                    decr_answer = PollAnswer.objects.filter(id=response.answer_id)
                else:
                    decr_abstain = True

            if request.POST.get("Answer",False):
                # this is a real answer, not an abstain
                answer_idx = request.POST.get("answer",None)
                if answer_idx:
                    answer = poll.get_ordered_answers()[int(answer_idx)].pollanswer
                else:
                    return Response( { "error": False } )
            else:
                answer = None

            response.answer = answer
            response.public = request.POST.get('public',False)
            response.save()

            if decr_answer:
                decr_answer.update(answer_count=F('answer_count')-1)
            if decr_abstain and answer:
                Poll.objects.filter( article__slug=slug ).update(abstain_count=F('abstain_count')-1)

            if answer:
                PollAnswer.objects.filter(id=response.answer_id).update(answer_count=F('answer_count')+1)
            else:
                Poll.objects.filter( article__slug=slug ).update(abstain_count=F('abstain_count')+1)

            poll.article.last_action = datetime.now()
            poll.article.save()
            vote_cast.send( sender=Poll, poll=poll, response=response, new_vote=new_vote, voter=request.user )
            return Response( { "error": False } )
        except Exception as e:
            return Response( { "error": True, "message": e.message } )        

bw_rest_api_submit_poll_answer = SubmitPollAnswer.as_view()

class GetRecentAnswers(APIView):
    """
    Get Recent Answers

    * Requires token authentication.
    * Only authenticated are able to access this view.
    """
    authentication_classes = (authentication.TokenAuthentication,)
    permission_classes = (permissions.IsAuthenticated,)

    def post(self, request, format=None):
        """
        Get Recent Answers
        """
        try:
            start = int(request.POST.get( "start", 0))
            end = int(request.POST.get( "end", 0))
            end = end+1
            recent_answers = PollResponse.objects.filter(user_id=request.user.id).exclude(poll__article__unpublished=True).exclude(poll__poll_type='Basic').order_by('-modified')[start:end]
            answers = []
            for answer in recent_answers:
                num_abstentions = answer.poll.pollresponse_set.filter(answer__isnull=True).count()
                if answer.answer:
                    answerCount = answer.answer.answer_count
                    answerText = answer.answer.answer_text
                else:
                    answerCount = num_abstentions
                    answerText = 'Abstain'
                    
                my_answer = {
                    'public': answer.public
                }
                if answer.answer:
                    my_answer['count'] = answer.answer.answer_count
                    my_answer['answer'] = answer.answer.answer_text
                else:
                    my_answer['count'] = num_abstentions
                    my_answer['answer'] = "Abstain"                 
                num_answers = answer.poll.article.num_poll_responses
                if answer.answer == None or (num_answers - num_abstentions) == 0:
                    answerPercent = 0
                else:
                    answerPercent = int( 100.0 * float( answer.answer.answer_count ) / (num_answers - num_abstentions)  + 0.5 )
                user = answer.poll.article.author
                name = user.first_name + ' ' + user.last_name
                avatar = bw_get_thumbnail( user.profile.get_avatar(), '21x21').url
                answerMap = answer.poll.get_ordered_answers()
                all_answers = []
                for item in answerMap:
                    if item.answer_count > 0:
                        itemPercent = int( 100.0 * float( item.answer_count ) / (num_answers - num_abstentions)  + 0.5 )
                        all_answers.append( { 'text': item.answer_text, 'count':item.answer_count, 'percent': itemPercent } )                
                item = {
                    'type': answer.poll.poll_type,
                    'slug': answer.poll.article.slug,
                    'lin_str': answer.poll.hand.lin_str(),
                    'answer': answerText,
                    'answer_count': answerCount,
                    'num_answers': num_answers,
                    'num_abstentions': num_abstentions,
                    'percent': answerPercent,
                    'public': answer.public,
                    'avatar': avatar,
                    'author': name,
                    'answers': all_answers,
                    'my_answer': my_answer
                }
                answers.append(item)
                    
            return Response( { "error": False, "recent_answers": answers } )
        except Exception as e:
            return Response( { "error": True, "message": e.message } )         

bw_rest_api_get_recent_answers = GetRecentAnswers.as_view()

class GetRecentPublished(APIView):
    """
    Get Recent Published Problems

    * Requires token authentication.
    * Only authenticated are able to access this view.
    """
    authentication_classes = (authentication.TokenAuthentication,)
    permission_classes = (permissions.IsAuthenticated,)

    def post(self, request, format=None):
        """
        Get Recent Published Problems
        """
        try:
            start = int(request.POST.get( "start", 0))
            end = int(request.POST.get( "end", 0))
            end = end+1
            recent_polls = Poll.objects.filter(article__author_id=request.user.id).exclude(article__unpublished=True).exclude(poll_type='Basic').order_by('-created')[start:end]
            answers = []
            for poll in recent_polls:
                num_abstentions = poll.pollresponse_set.filter(answer__isnull=True).count()
                num_answers = poll.article.num_poll_responses
                user = poll.article.author
                name = user.first_name + ' ' + user.last_name
                avatar = bw_get_thumbnail( user.profile.get_avatar(), '21x21').url
                answerMap = poll.get_ordered_answers()
                all_answers = []
                for item in answerMap:
                    if item.answer_count > 0:
                        itemPercent = int( 100.0 * float( item.answer_count ) / (num_answers - num_abstentions)  + 0.5 )
                        all_answers.append( { 'text': item.answer_text, 'count':item.answer_count, 'percent': itemPercent } )
                my_response = get_object_or_none( PollResponse, user_id=request.user.id, poll_id=poll.id )        
                my_answer = None
                if my_response:
                    my_answer = {
                        'public': my_response.public
                    }
                    if my_response.answer:
                        my_answer['count'] = my_response.answer.answer_count
                        my_answer['answer'] = my_response.answer.answer_text
                    else:
                        my_answer['count'] = num_abstentions
                        my_answer['answer'] = "Abstain"                            
                item = {
                    'type': poll.poll_type,
                    'slug': poll.article.slug,
                    'lin_str': poll.hand.lin_str(),
                    'num_answers': num_answers,
                    'num_abstentions': num_abstentions,
                    'avatar': avatar,
                    'author': name,
                    'answers': all_answers,
                    'my_answer': my_answer                    
                }
                answers.append(item)
                    
            return Response( { "error": False, "recent_answers": answers } )
        except Exception as e:
            return Response( { "error": True, "message": e.message } )         

bw_rest_api_get_recent_published = GetRecentPublished.as_view()

class CreateNewProblem(APIView):
    """
    Create New Problem

    * Requires token authentication.
    * Only authenticated are able to access this view.
    """
    authentication_classes = (authentication.TokenAuthentication,)
    permission_classes = (permissions.IsAuthenticated,)

    def post(self, request, format=None):
        """
        Create New Problem
        """
        try:
            poll = None
            pollType = request.POST.get( "type", "Bidding" )
            
            article = get_object_or_none( Article, slug=None )

            if is_probated( request.user ):
                return Response( { "error": True, "message": "You cannot create or edit problems while suspended." } )
            if is_banned( request.user ):
                return Response( { "error": True, "message": "You cannot create or edit problems while banned." } )
            
            if pollType == "Bidding":
                form = BiddingProblemForm( request.user, request.POST or None, instance=article )
            else:
                form = LeadProblemForm( request.user, request.POST or None, instance=article )
                    
            if form.is_valid():
                article = form.save(commit=False)
                if not form.cleaned_data.get("author",None):
                    article.author = request.user
                article.quick = True
                article.hide_on_front_page = True
                article.save()
                article.post_inline_save()

                if pollType == "Bidding":
                    answers = [
                        '1c','1d','1h','1s','1n',
                        '2c','2d','2h','2s','2n',
                        '3c','3d','3h','3s','3n',
                        '4c','4d','4h','4s','4n',
                        '5c','5d','5h','5s','5n',
                        '6c','6d','6h','6s','6n',
                        '7c','7d','7h','7s','7n',
                        'X','R','P'
                    ]
                else:
                    answers = [
                        'C2', 'C3', 'C4', 'C5', 'C6', 'C7', 'C8', 'C9', 'CT', 'CJ', 'CQ', 'CK', 'CA', 'Cx',
                        'D2', 'D3', 'D4', 'D5', 'D6', 'D7', 'D8', 'D9', 'DT', 'DJ', 'DQ', 'DK', 'DA', 'Dx',
                        'H2', 'H3', 'H4', 'H5', 'H6', 'H7', 'H8', 'H9', 'HT', 'HJ', 'HQ', 'HK', 'HA', 'Hx',
                        'S2', 'S3', 'S4', 'S5', 'S6', 'S7', 'S8', 'S9', 'ST', 'SJ', 'SQ', 'SK', 'SA', 'Sx',
                    ]                             
                poll = get_object_or_none( Poll, article=article )
                if poll:
                    poll.content=form.cleaned_data["description"]
                    poll.auction=form.cleaned_data["auction"]
                    poll.vul=form.cleaned_data["vul"]
                    poll.dealer=form.cleaned_data["dealer"]
                    poll.scoring=form.cleaned_data["scoring"]
                    poll.hand=form.cleaned_data["hand"]
                    poll.save()
                else:
                    poll = Poll.objects.create( poll_type=pollType, content=form.cleaned_data["description"], auction=form.cleaned_data['auction'], vul=form.cleaned_data['vul'], scoring = form.cleaned_data['scoring'], dealer=form.cleaned_data['dealer'], hand=form.cleaned_data['hand'], article=article )
                    for i,answer in enumerate(answers):
                        PollAnswer.objects.create( order=i, question=poll.multiplechoicequestion_ptr, answer_text=answer )
                # Preview will be done on client side. Publish immediately
                article.toggle_publish()
                return Response( { "error": False, "slug": article.slug } )
            return Response( { "error": True, "message": "Form data is not valid." } )
        except Exception as e:
            return Response( { "error": True, "message": e.message } )

bw_rest_api_create_new_problem = CreateNewProblem.as_view()

class GetProblem(APIView):
    """
    Get a Problem with give slug

    * Requires token authentication.
    * Only authenticated are able to access this view.
    """
    authentication_classes = (authentication.TokenAuthentication,)
    permission_classes = (permissions.IsAuthenticated,)

    def get(self, request, slug, format=None):
        """
        Get a Problem with given slug
        """
        try:
            problem = get_object_or_none( Poll, article__slug=slug )
            if not problem:
                return Response( { "error": True, "message": "No such Poll" } )
            my_response = get_object_or_none( PollResponse, user_id=request.user.id, poll_id=problem.id )        
            user = problem.article.author
            name = user.first_name + ' ' + user.last_name
            avatar = bw_get_thumbnail( user.profile.get_avatar(), '21x21').url
            content = render_to_string('bw_restapi/problem_description.html', { 'problem': problem })
            answerMap = problem.get_ordered_answers()
            answers = []
            num_abstentions = problem.pollresponse_set.filter(answer__isnull=True).count()
            my_answer = None
            if my_response:
                my_answer = {
                    'public': my_response.public
                }
                if my_response.answer:
                    my_answer['answer'] = my_response.answer.answer_text
                else:
                    my_answer['answer'] = "Abstain"            
            for answer in answerMap:
                if answer.answer_count > 0:
                    if answer.answer_text.lower() == my_answer['answer'].lower():
                        my_answer['count'] = answer.answer_count;
                    answers.append( { 'text': answer.answer_text, 'count':answer.answer_count } )
            item = {
                'my_answer': my_answer,
                'num_answers': problem.article.num_poll_responses,
                'abstentions': num_abstentions,
                'num_abstentions': num_abstentions,
                'answers': answers,
                'slug': problem.article.slug,
                'description': content,
                'type': problem.poll_type,
                'lin_str': problem.hand.lin_str(),
                'author': name,
                'avatar': avatar,
                'scoring': problem.scoring,
                'vulnerability': problem.vul,
                'auction': problem.auction,
                'dealer': problem.dealer
            }
            item["error"] = False

            return Response( item )
        except Exception as e:
            return Response( { "error": True, "message": e.message } )          

bw_rest_api_get_problem = GetProblem.as_view()


class GetResponses(APIView):
    """
    Get the names of responders for a poll

    * Requires token authentication.
    * Only authenticated are able to access this view.
    """
    authentication_classes = (authentication.TokenAuthentication,)
    permission_classes = (permissions.IsAuthenticated,)

    def get(self, request, slug, format=None):
        """
        Get the names of responders for a poll
        """
        try:
            problem = get_object_or_none( Poll, article__slug=slug )
            if not problem:
                return Response( { "error": True, "message": "No such Poll" } )            
            my_response = get_object_or_none( PollResponse, user_id=request.user.id, poll_id=problem.id )
            if not my_response:
                return Response({ 'success': False, 'message': 'Sorry, you have to vote publicly to see public poll results.' })
            answerMap = problem.get_ordered_answers()
            responses = []
            for answer in answerMap:
                if answer.answer_count > 0:
                    item = {
                        'answer_text': answer.answer_text,
                        'num_private_responses': answer.pollresponse_set.filter(public=False).count(),
                        'public_responses': []
                    }
                    
                    for response in answer.pollresponse_set.filter(public=True).order_by("-user__stats__num_followers"):
                        user = response.user
                        name = user.first_name + ' ' + user.last_name
                        item[ 'public_responses' ].append(name)
                    responses.append(item)
            num_abstentions = problem.pollresponse_set.filter(answer__isnull=True).count()
            if num_abstentions > 0:
                item = {
                    'answer_text': 'Abstain',
                    'num_private_responses': problem.pollresponse_set.filter(public=False,answer__isnull=True).count(),
                    'public_responses': []
                }
                for response in problem.pollresponse_set.filter(public=True,answer__isnull=True):
                    user = response.user
                    name = user.first_name + ' ' + user.last_name
                    item[ 'public_responses' ].append(name)
                responses.append(item)            
            return Response({ 'responses': responses, 'success': True, 'type': problem.poll_type })
        except Exception as e:
            return Response( { "error": True, "message": e.message } )          
    

bw_rest_api_get_responses = GetResponses.as_view()

### These are for debugging only. Should not be kept for production

class UnpublishProblem(APIView):
    """
    Unpublish problem - only for debug

    * Requires token authentication.
    * Only authenticated are able to access this view.
    """
    authentication_classes = (authentication.TokenAuthentication,)
    permission_classes = (permissions.IsAuthenticated,)

    def get(self, request, slug, format=None):
        """
        Get a Problem with given slug
        """
        try:
            problem = get_object_or_none( Poll, article__slug=slug )
            if not problem:
                return Response( { "error": True, "message": "No such Poll" } )                 
            if not problem.article.unpublished:
                problem.article.toggle_publish()
                return Response({ "error": False  })
            return Response({ "error": True, 'message': 'Already unpublished' })
        except Exception as e:
            return Response( { "error": True, "message": e.message } )         

bw_rest_api_unpublish_problem = UnpublishProblem.as_view()

class UpdateCount(APIView):
    """
    Update count - only for debug

    * Requires token authentication.
    * Only authenticated are able to access this view.
    """
    authentication_classes = (authentication.TokenAuthentication,)
    permission_classes = (permissions.IsAuthenticated,)

    def get(self, request, slug, format=None):
        """
        Update count
        """
        try:
            problem = get_object_or_none( Poll, article__slug=slug )
            if not problem:
                return Response( { "error": True, "message": "No such Poll" } )
            problem.article.num_poll_responses = problem.pollresponse_set.count()
            problem.article.save()
            return Response({ "error": False  })
        except Exception as e:
            return Response( { "error": True, "message": e.message } )         

bw_rest_api_update_count = UpdateCount.as_view()

                             

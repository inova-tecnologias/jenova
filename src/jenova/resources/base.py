import jwt, base64, json
from collections import namedtuple
from werkzeug.exceptions import InternalServerError
from flask_restful import reqparse, request, Resource, abort
from functools import wraps
from time import sleep

from jenova.models import UserSchema, Scope
from jenova.components import Security, InvalidCredentials, CallLogger, JwtInconsistentDataError

from jenova.components.tasks import (
  update_cos_into_domain_zimbra_task, create_domain_zimbra_task,
  delete_domain_zimbra_task, create_delegated_zimbra_admin_task
)

logger = CallLogger.logger()

TASK_TYPES = ['createzimbradomains']
QUERY_FILTER_IDS = ['id', 'client_id', 'authentication_id', 'domain_id', 'service_id']
RESERVED_NAMES = ['inova', 'jenova', 'inovatec', 'jnv', 'all']
DEFAULT_SCOPES = [
  'dns',
  'domain',
  'service',
  'store',
  'users',
  'zimbra',
  'client',
  'permissions'
]
PERMS = ['write', 'read', 'edit', 'delete']

def abort_if_obj_doesnt_exist(filter_by, target, model_object):
  if filter_by in QUERY_FILTER_IDS:
    try:
      target = int(target)
    except ValueError, e:
      raise
  query = { filter_by : target }
  result = model_object.query.filter_by(**query).first()
  if not result:
    abort(404, message='Could not find object: %s' % target)
  return result

def exception_handler(f):
  @wraps(f)
  def decorated(*args, **kwargs):
    resource = f.__self__
    # Check for violation!
    # TODO: This method will query for the resources on the most of the resource classes,
    # maybe it is possible to pass the model object throughout this decorated method
    resource.is_forbidden(**kwargs)
    # Check permissions only if isn't an admin
    if not resource.is_an_admin:
      if f.__name__ == 'get':
        resource.can_read(resource.scope)
      elif f.__name__ == 'put':
        resource.can_edit(resource.scope)
      elif f.__name__ == 'post':
        resource.can_write(resource.scope)
      elif f.__name__ == 'delete':
        resource.can_delete(resource.scope)

    if request.data and type(request.json) != dict:
      abort(415, message = 'Expecting JSON')
    return f(*args, **kwargs)
  return decorated

class BaseResource(Resource):
  method_decorators = [exception_handler]

  def __init__(self, filters, default_filter='name', **kwargs):
    self.logger = logger
    parser = reqparse.RequestParser()
    parser.add_argument('filter_by', type=str, location='args')
    self.filter_by = parser.parse_args().get('filter_by') or default_filter
    if self.filter_by not in filters:
      err_message = 'Wrong query filter specified %s. Accept only: %s' % (self.filter_by, ', '.join(filters))
      abort(400, message=err_message)
    self.parser = reqparse.RequestParser()

    self.jwt_payload = self.check_auth()

  def check_auth(self):
    auth = request.headers.get('Authorization', None)
    message = ''
    if not auth:
      abort(401, message = 'Authorization header is expected')

    parts = auth.split()

    if parts[0].lower() != 'bearer':
      message = 'Authorization header must start with Bearer'
    elif len(parts) == 1:
      message = 'Token not found'
    elif len(parts) > 2:
      message = 'Authorization header must be Bearer + \s + token'

    if message:
      abort(401, message = message)

    token = parts[1]
    try:
      payload = jwt.decode(
        token,
        Security.get_jwt_skey(),
        algorithms = ['HS256']
      )
    except jwt.ExpiredSignature:
      message = 'token is expired'
    except jwt.InvalidAudienceError:
      message = 'incorrect audience'
    except jwt.DecodeError:
      message = 'token signature is invalid'

    if message:
      abort(401, message = message)

    self.logger.debug('Access granted for %s!' % payload['user']['login'])

    return payload

  @property
  def request_user_login(self):
    return self.jwt_payload['user']['login']

  @property
  def is_admin(self):
    return self.jwt_payload['user']['admin']

  @property
  def is_global_admin(self):
    return self.jwt_payload['user']['global_admin']

  @property
  def is_an_admin(self):
    return self.is_admin or self.is_global_admin

  @property
  def request_user_client_id(self):
    return self.jwt_payload['user']['client_id']

  @property
  def request_user_reseller_id(self):
    # It's a reseller admin user
    if self.jwt_payload['user']['reseller']:
      return self.jwt_payload['user']['reseller']['id']
    return self.jwt_payload['user']['client']['reseller']['id']

  @property
  def request_user_id(self):
    return self.jwt_payload['user']['id']

  ### PERMISSIONS METHODS ###
  """
  # It is possible to override these methods on each Resource classes.
  # This will give more flexibility by implementing your own behavior.
  # Edit this methods on this class only if you need to change the whole logic,
  # otherwise, override this methods in resources classes that inherit from this class.
  # For disabling the behavior, override this method with the pass operator:
  # def can_read():
  #   pass
  """
  def can_read(self, scope_name):
    """ Check if it has permission to read. Has to be evaluated on every GET HTTP methods.
    """
    has_read_perm = False
    for perm in self.jwt_payload['user']['permissions']:
      if perm['scope']['name'] == scope_name:
        has_read_perm = True
        if not perm.get('read'):
          has_read_perm = False
        break
    if not has_read_perm:
      abort(403, message = 'Permission denied! Does not have proper permission.')

  def can_write(self, scope_name):
    """ Check if it has permission to write. Has to be evaluated on every POST HTTP methods.
    """
    has_write_perm = False
    for perm in self.jwt_payload['user']['permissions']:
      if perm['scope']['name'] == scope_name:
        has_write_perm = True
        if not perm.get('write'):
          has_write_perm = False
        break
    if not has_write_perm:
      abort(403, message = 'Permission denied! Does not have proper permission.')

  def can_edit(self, scope_name):
    """ Check if it has permission to edit. Has to be evaluated on every PUT/PATCH HTTP methods.
    """
    has_edit_perm = False
    for perm in self.jwt_payload['user']['permissions']:
      if perm['scope']['name'] == scope_name:
        has_edit_perm = True
        if not perm.get('edit'):
          has_edit_perm = False
        break
    if not has_edit_perm:
      abort(403, message = 'Permission denied! Does not have proper permission.')

  def can_delete(self, scope_name):
    """ Check if it has permission to delete. Has to be evaluated on every DELETE HTTP methods.
    """
    has_del_perm = False
    for perm in self.jwt_payload['user']['permissions']:
      if perm['scope']['name'] == scope_name:
        has_del_perm = True
        if not perm.get('delete'):
          has_del_perm = False
        break
    if not has_del_perm:
      abort(403, message = 'Permission denied! Does not have proper permission.')

  def is_forbidden(self, **kwargs):
    """ Check if the resource is allowed by a global admin user. It must be overrided if the user
    is not a global admin, the contraints must be evaluated accordingly,
    must ensure if the request user is the owner of the requested resource.
    :param kwargs: The resource attributes for validating the contraints
    """
    if not self.is_global_admin: abort(403, message = 'Permission denied! Does not have enough permissions.')

class TaskResource(BaseResource):
  def __init__(self):
    filters = ['id', 'name']
    super(TaskResource, self).__init__(filters)

  def is_forbidden(self, **kwargs): pass
  def can_read(self): pass

  def get(self, task_type, task_id):
    if task_type == 'createzimbradomains':
      task = create_domain_zimbra_task.AsyncResult(task_id)
    elif task_type == 'createdelegatedzimbra':
      task = create_delegated_zimbra_admin_task.AsyncResult(task_id)
    else:
      abort(400, message = 'Wrong task_type specified')

    try:
      task_state = task.state
      task_executed = task.ready()
    except Exception:
      task_state = 'ERROR'
      task_executed = True

    return {
      'response' : {
        'task_state' : task_state,
        'task_executed' : task_executed
      }
    }
from datetime import datetime
import uuid, jwt

from flask_restful import abort, Resource, reqparse, request
from jenova.resources.base import BaseResource, abort_if_obj_doesnt_exist
from jenova.components import Security, db
from jenova.models import Client, User, Scope, ScopeSchema, Permissions, UserSchema, Reseller

class ScopeListResource(BaseResource):
  def __init__(self):
    filters = ['id', 'name']
    super(ScopeListResource, self).__init__(filters)

  def get(self):
    self.parser.add_argument('limit', type=int, location='args')
    self.parser.add_argument('offset', type=int, location='args')
    reqdata = self.parser.parse_args()
    offset, limit = reqdata.get('offset') or 0, reqdata.get('limit') or 25

    scopes = Scope.query\
      .offset(offset)\
      .limit(limit)\
      .all()

    if not scopes:
      abort(404, message = 'Could not find any scopes')

    return {
      'response' : {
        'scopes' : ScopeSchema(many=True).dump(scopes).data
      }
    }

class ScopeResource(BaseResource):
  def __init__(self):
    filters = ['login', 'id']
    super(ScopeResource, self).__init__(filters, default_filter='login')

  def get(self, scope_name):
    if not self.is_global_admin:
      abort(401, message = 'Permission Denied! User does not has permission to this resource.')

    scope = abort_if_obj_doesnt_exist('name', scope_name, Scope)
    hasmany = False
    if type(scope) == list:
      hasmany = True
    scope_result = ScopeSchema(many=hasmany).dump(scope)
    return { 
      'response' : { 
        'scope' : scope_result.data
      }
    }

  def post(self, scope_name):
    if not self.is_global_admin:
      abort(401, message = 'Permission Denied! User does not has permission to this resource.')

    if Scope.query.filter_by(name = scope_name).first():
      abort(409, message = 'Scope %s already exists' % scope_name)

    scope = Scope(name = scope_name)
    db.session.add(scope)
    db.session.commit()

    return {
      'response' : {
        'scope_id' : scope.id
      }
    }, 201

  def delete(self, scope_name):
    if not self.is_global_admin:
      abort(401, message = 'Permission Denied! User does not has permission to this resource.')

    scope = abort_if_obj_doesnt_exist('name', scope_name, Scope)
    db.session.delete(scope)
    db.session.commit()

class ScopeListUserResource(BaseResource):
  def __init__(self):
    filters = ['id', 'login']
    super(ScopeListUserResource, self).__init__(filters)

  def get(self, scope_name):
    self.parser.add_argument('limit', type=int, location='args')
    self.parser.add_argument('offset', type=int, location='args')
    reqdata = self.parser.parse_args()
    offset, limit = reqdata.get('offset') or 0, reqdata.get('limit') or 25

    scope = abort_if_obj_doesnt_exist('name', scope_name, Scope)
    users = User.query.filter_by(scope_id = scope.id).limit(offset, limit).all()
    if not users:
      abort(404, message = 'Could not find any users for scope: %s' % scope_name)

    return {
      'response' : {
        'scope_id' : scope.id,
        'users' : UserSchema(many=True).dump(users).data
      }
    }

class ScopeUserResource(BaseResource):
  def __init__(self):
    filters = ['id', 'login']
    super(ScopeUserResource, self).__init__(filters)

  def get(self, scope_name, user):
    q = db.session.query(Scope, User)\
      .filter(Scope.id == user.scope_id)\
      .filter(user.name == user, scope.name == scope.name).first()

    if not q:
      abort(404, message = 'Could not find scope "%s" for user "%s"' % (scope_name, user))

    return '', 204

  def post(self, scope_name, user):
    scope = abort_if_obj_doesnt_exist('name', scope_name, Scope)
    user = abort_if_obj_doesnt_exist(self.filtery_by, user, User)
    if user in scope.user:
      abort(409, message = 'User %s already belong to this scope %s' % (user.login, scope_name) )

    scope.user.append(user)
    db.session.commit()

    return '', 204

  def delete(self, scope_name, user):
    scope = abort_if_obj_doesnt_exist('name', scope_name, Scope)
    user = abort_if_obj_doesnt_exist(self.filtery_by, user, User)

    # search for object matching scope_id in list: scope.user
    user_result = filter(lambda u : u.scope_id == scope.id, scope.user)
    if not user_result:
      abort(404, message='Could not find user %s into scope %s' % (user.login, scope.name))

    scope.user.remove(user_result[0])
    db.session.commit()

    return '', 204

class UserListResource(BaseResource):
  def __init__(self):
    filters = ['id', 'name']
    super(UserListResource, self).__init__(filters)

  @property
  def scope(self):
    return 'users'

  # Overrided
  def is_forbidden(self, **kwargs): pass

  def get(self, **kwargs):
    self.parser.add_argument('limit', type=int, location='args')
    self.parser.add_argument('offset', type=int, location='args')
    by_name_query = kwargs.get('by_name_query') or ''
    reqdata = self.parser.parse_args()
    offset, limit = reqdata.get('offset') or 0, reqdata.get('limit') or 25

    if self.is_global_admin:
      users = User.query \
        .filter(User.name.like('%' + by_name_query + '%') | User.email.like('%' + by_name_query + '%') | User.login.like('%' + by_name_query + '%'))\
        .offset(offset) \
        .limit(limit) \
        .all()
    elif self.is_admin:
      # Equivalent:
      # select u.login from user as u left join client c on c.id = u.client_id 
      # left join reseller r on u.reseller_id = r.id where c.reseller_id = <id> or r.id = <id>;
      users = User.query \
        .outerjoin(Client, Client.id == User.client_id) \
        .outerjoin(Reseller, User.reseller_id == Reseller.id) \
        .filter((Client.reseller_id == self.request_user_reseller_id) \
          | (Reseller.id == self.request_user_reseller_id)) \
        .filter(User.name.like('%' + by_name_query + '%') | User.email.like('%' + by_name_query + '%') | User.login.like('%' + by_name_query + '%'))\
        .offset(offset) \
        .limit(limit) \
        .all()
    else:
      users = User.query.filter_by(id = self.request_user_id)

    if not users:
      abort(404, message = 'Could not find any users')

    return {
      'response' : {
        'users' : UserSchema(many=True).dump(users).data
      }
    }

class UserResource(BaseResource):  
  def __init__(self):
    filters = ['login', 'id']
    super(UserResource, self).__init__(filters, default_filter='login')

  @property
  def scope(self):
    return 'users'

  # Overrided
  def is_forbidden(self, target_auth):
    """ Check for access rules:
    A global admin must not have any restrictions.
    A requester must be an admin.
    A requester must have access to your own users.
    """
    if self.is_global_admin: return
    # Only and admin can DELETE and CREATE new users
    if not self.is_admin and request.method in ['POST', 'DELETE']:
      abort(403, message = 'Permission denied. Does not have enough permissions')

    if not target_auth:
      abort(400, message = 'Could not find "target_auth"')

    if request.method == 'POST': 
      return

    user = abort_if_obj_doesnt_exist('login', target_auth, User)
    if request.method == 'DELETE':
      if user.reseller_id:
        abort(403, message = 'Permission denied! Could not delete a reseller login!')

    if user.client and user.client.reseller_id != self.request_user_reseller_id:
      abort(403, message = 'Permission denied! Does not have enough permissions')

    if user.reseller_id and user.reseller_id != self.request_user_reseller_id:
      abort(403, message = 'Permission denied! Does not have enough permissions')

  def get(self, target_auth):
    user = abort_if_obj_doesnt_exist(self.filter_by, target_auth, User)
    return {
      'response' : {
        'users' : UserSchema().dump(user).data
      }
    }

  def delete(self, target_auth):
    user = abort_if_obj_doesnt_exist(self.filter_by, target_auth, User)
    if user.reseller_id:
      abort(400, message = 'Cannot delete a reseller user.')

    db.session.delete(user)
    db.session.commit()
    return '', 204

  def post(self, target_auth):
    target_auth = str(target_auth)
    self.parser.add_argument('client_name', type=str, required=True)
    self.parser.add_argument('name', type=str, required=True)
    self.parser.add_argument('email', type=str, required=True)
    self.parser.add_argument('password', type=str, required=True)
    self.parser.add_argument('admin', type=bool, default=False)
    self.parser.add_argument('enable_api', type=bool, default=False)
    self.parser.add_argument('global_admin', type=bool, default=False)
    reqdata = self.parser.parse_args(strict=True)
    
    client = abort_if_obj_doesnt_exist('name', reqdata['client_name'], Client)

    if not self.is_global_admin:
      if client.reseller_id != self.request_user_reseller_id:
        abort(403, message = 'Permission denied! Does not have enough permission.')
      # Disable global_admin switch if the user is not a global admin user
      reqdata['global_admin'] = False 

    user = User(
      login = target_auth,
      name = reqdata['name'],
      email = reqdata['email'],
      password = Security.hash_password(reqdata['password']),
      api_enabled = reqdata['enable_api'],
      admin = reqdata['admin'],
      global_admin = reqdata['global_admin']
    )

    if User.query.filter_by(login=target_auth).first():
      abort(409, message='The user {} already exists'.format(target_auth))

    # Global Admin does not belong to any client or reseller
    if not reqdata['global_admin']:
      user.client_id = client.id
    
    db.session.add(user)
    db.session.commit()
    user = User.query.filter_by(login=target_auth).one()

    return {
      'response' : { 
        'user_id' : user.id
      } 
    }, 201

  def put(self, target_auth):
    user = abort_if_obj_doesnt_exist(self.filter_by, target_auth, User)
    self.parser.add_argument('client_name', type=str)
    self.parser.add_argument('enabled', type=bool)
    self.parser.add_argument('admin', type=bool)
    self.parser.add_argument('api_enabled', type=bool)
    self.parser.add_argument('password', type=str)
    self.parser.add_argument('email', type=str)
    self.parser.add_argument('name', type=str)
    self.parser.add_argument('desc', type=str)
    reqdata = self.parser.parse_args()

    client_id = None
    if reqdata.get('client_name'):
      if user.reseller_id:
        abort(409, message = 'Could not associate a reseller user to a client')
      elif not self.is_an_admin:
        abort(403, message = 'Permission denied! Could not change state of "client_id"')
      elif user.global_admin:
        abort(409, message = 'Could not associate a global admin user to a client or reseller')
      client = abort_if_obj_doesnt_exist('name', reqdata['client_name'], Client)
      client_id = client.id

    get_bool = lambda x, y: y if x == None else x

    if reqdata.get('admin') and not self.is_an_admin:
      abort(403, message = 'Permission denied! Does not have enough permissions')
    user.client_id = client_id or user.client_id
    user.enabled = get_bool(reqdata.get('enabled'), user.enabled)
    user.admin = get_bool(reqdata.get('admin'), user.admin)
    user.api_enabled = get_bool(reqdata.get('api_enabled'), user.api_enabled)
    if reqdata.get('password'):
      user.password = Security.hash_password(reqdata.get('password'))
    user.email = reqdata.get('email') or user.email
    user.name = reqdata.get('name') or user.name
    user.desc = reqdata.get('desc') or user.desc
    db.session.commit()
    return '', 204

class UserChangeStateResource(BaseResource):
  def __init__(self):
    filters = ['id', 'login']
    super(UserChangeStateResource, self).__init__(filters, default_filter='login')

  def post(self, target_auth):
    user = abort_if_obj_doesnt_exist(self.filter_by, target_auth, User)
    end_path = request.path.split('/')[-1:][0]

    if end_path == 'globaladmin':
      user.global_admin = True
    elif end_path == 'admin':
      user.admin = True
    elif end_path == 'api':
      user.api_enabled = True
    else:
      abort(405, message = 'Method not allowed.')
    db.session.commit()

    return '', 204

  def delete(self, target_auth):
    user = abort_if_obj_doesnt_exist(self.filter_by, target_auth, User)
    end_path = request.path.split('/')[-1:][0]

    if end_path == 'globaladmin':
      user.global_admin = False
    elif end_path == 'admin':
      user.admin = False
    elif end_path == 'api':
      user.api_enabled = False
    else:
      abort(405, message = 'Method not allowed.')
    db.session.commit()

    return '', 204

class PermissionsResource(BaseResource):
  def __init__(self):
    filters = ['name', 'id']
    super(PermissionsResource, self).__init__(filters, default_filter='name')

  @property
  def scope(self):
    return 'permissions'

  # Overrided
  def is_forbidden(self, scope_name, user):
    """ Check for access rules:
    A global admin must not have any restrictions.
    A requester must be an admin.
    A requester must have access to your own users.
    """
    if self.is_global_admin: return
    if not self.is_admin:
      abort(403, message = 'Permission denied. Does not have enough permissions.')

    if not user:
      abort(400, message = 'Could not find "user"')

    user = abort_if_obj_doesnt_exist('login', user, User)
    # It's a reseller login
    if user.reseller_id and self.request_user_reseller_id != user.reseller_id:
      abort(403, message = 'Permission denied! The requester does not have permission to access this user.')

    if user.client and self.request_user_reseller_id != user.client.reseller_id:
      abort(403, message = 'Permission denied! The requester does not have enough permissions')      

  def get(self, scope_name, user):
    user = abort_if_obj_doesnt_exist('login', user, User)
    abort_if_obj_doesnt_exist('name', scope_name, Scope)
    return { 'response' : UserSchema().dump(user).data }

  def put(self, scope_name, user):
    user = abort_if_obj_doesnt_exist('login', user, User)
    scope = abort_if_obj_doesnt_exist('name', scope_name, Scope)

    self.parser.add_argument('read', type=bool)
    self.parser.add_argument('write', type=bool)
    self.parser.add_argument('edit', type=bool)
    self.parser.add_argument('delete', type=bool)
    reqdata = self.parser.parse_args()

    perm = Permissions.query.filter_by(user_id = user.id, scope_id = scope.id).first()
    if not perm:
      perm = Permissions(
        read = reqdata.get('read') or False,
        write = reqdata.get('write') or False,
        delete = reqdata.get('delete') or False,
        edit = reqdata.get('edit') or False
      )
    perm.read = reqdata.get('read') or False
    perm.write = reqdata.get('write') or False
    perm.delete = reqdata.get('delete') or False
    perm.edit = reqdata.get('edit') or False

    perm.user_id = user.id
    perm.scope_id = scope.id

    is_user_in_scope = False
    for scope_user in scope.user:
      if scope_user.id == user.id:
        is_user_in_scope = True

    if not is_user_in_scope:
      # This user must belong to this scope
      scope.user.append(user)
      
    db.session.add(perm)
    db.session.commit()

    return {
      'response' : {
        'perm_id' : perm.id
      }
    }

  # Perm ON
  def post(self, scope_name, user):
    user = abort_if_obj_doesnt_exist('login', user, User)
    scope = abort_if_obj_doesnt_exist(self.filter_by, scope_name, Scope)

    perm = Permissions.query.filter_by(user_id = user.id, scope_id = scope.id).first()
    if not perm:
      perm = Permissions(
        read = False,
        write = False,
        delete = False,
        edit = False
      )
      db.session.add(perm)
    
    perm.user_id = user.id
    perm.scope_id = scope.id

    end_path = request.path.split('/')[-1:][0]

    if end_path == 'read':
      perm.read = True
    elif end_path == 'write':
      perm.write = True
    elif end_path == 'edit':
      perm.edit = True
    elif end_path == 'delete':
      perm.delete = True
    else:
      abort(405, message = 'Method not allowed.')

    db.session.commit()
    return {
      'response' : {
        'perm_id' : perm.id
      }
    }

  # Perm OFF
  def delete(self, scope_name, user):
    user = abort_if_obj_doesnt_exist('login', user, User)
    scope = abort_if_obj_doesnt_exist(self.filter_by, scope_name, Scope)

    perm = Permissions.query.filter_by(user_id = user.id, scope_id = scope.id).first()
    if not perm:
      abort(404, message = 'Could not find any permission for user "%s" and scope "%s"' % (user.name, scope_name))

    end_path = request.path.split('/')[-1:][0]

    if end_path == 'read':
      perm.read = False
    elif end_path == 'write':
      perm.write = False
    elif end_path == 'edit':
      perm.edit = False
    elif end_path == 'delete':
      perm.delete = False
    else:
      abort(405, message = 'Method not allowed.')

    db.session.commit()
    return {
      'response' : {
        'perm_id' : perm.id
      }
    }

class AuthenticationResource(Resource):
  def post(self):
    parser = reqparse.RequestParser()
    parser.add_argument('username', type=str, required=True)
    parser.add_argument('password', type=str, required=True)
    reqdata = parser.parse_args(strict=True)

    user = User.query.filter_by(login = reqdata['username']).first()
    if not user:
      abort(401, message = 'Wrong credentials')

    if not Security.check_password(user.password, reqdata['password']):
      abort(401, message = 'Wrong credentials')
    user.scopes = Scope.query.all()

    enc_jwt = jwt.encode({'user' : UserSchema().dump(user).data}, Security.get_jwt_skey(), algorithm='HS256')

    return { 
      'response' : { 
        'token' : enc_jwt
      }
    }
import ast
from flask_restful import abort, request
from jenova.resources.base import BaseResource, abort_if_obj_doesnt_exist
from jenova.models import Cos, CosSchema, Features, Service, Domain
from jenova.components import db
from jenova.components.tasks import create_cos_zimbra_task, modify_cos_zimbra_task, delete_cos_zimbra_task
from jenova.components.zimbra import ZimbraRequest

class DomainCosResource(BaseResource):
  def __init__(self):
    filters = ['id', 'name']
    super(DomainCosResource, self).__init__(filters)
  
  @property
  def scope(self):
    return 'zimbra'

  def get(self, service_name, domain_name):
    service = abort_if_obj_doesnt_exist('name', service_name, Service)

    if not service.service_type == 'ZIMBRA':
      abort(400, message = 'Only Zimbra type is allowed')

    cred = service.credentials
    if not cred:
      abort(400, message = 'Could not find any credentials for the service %s' % service.name)
    admin_user, admin_password = cred.identity, cred.secret

    zr = ZimbraRequest(
      admin_url = service.service_api, 
      admin_user = admin_user, 
      admin_pass = admin_password
    )

    res = zr.getCOSAccountUsage(domain_name)

    # when request is made by an admin will return service defaults COS as well.
    if self.is_admin or self.is_global_admin:
      cos = Cos.query.filter(Cos.service_id == service.id)

      for c in cos:
        not_present = True
        for r in res:
          if r['id'] == c.zimbra_id:
            not_present = False
        
        if not_present:
          data = {
            'users' : 0,
            'limit' : 0,
            'id' : c.zimbra_id,
            'name' : c.name
          }
          res.append(data)
    
    return { 'response' : res }

  def put(self, service_name, domain_name):
    service = abort_if_obj_doesnt_exist('name', service_name, Service)

    if not service.service_type == 'ZIMBRA':
      abort(400, message = 'Only Zimbra type is allowed')

    cred = service.credentials
    if not cred:
      abort(400, message = 'Could not find any credentials for the service %s' % service.name)
    admin_user, admin_password = cred.identity, cred.secret

    self.parser.add_argument('cos', action="append", required=True)
    reqdata = self.parser.parse_args()

    zr = ZimbraRequest(
      admin_url = service.service_api, 
      admin_user = admin_user, 
      admin_pass = admin_password
    )

    cos_limit = []
    for cos in reqdata['cos']:
      cos = ast.literal_eval(cos)
      #('zimbraDomainCOSMaxAccounts', '8e97e282-8aa0-4ac4-96fb-7e2e7620c0a4:200')
      zdcma = ('zimbraDomainCOSMaxAccounts', '%s:%s' % (cos['id'], cos['limit']))
      if cos['limit'] > 0:
        cos_limit.append(zdcma)

    try:
      d_id = zr.getDomainId(domain=domain_name)
      res = zr.modifyDomain(domain_id = d_id, attrs=cos_limit)
      return { 'response' : 'ok' }, 201
    except e:
      abort(501, message = 'Something went wrong :%s' % e)
    return { 'response' : 'ok'}


  # Overrided
  def is_forbidden(self, **kwargs):
    """ Check for access rules:
    A global admin must not have any restrictions.
    A requester must have access of your own domains (reseller) only if is an admin
    A requester must have access of your own domains (client) 
    """
    if self.is_global_admin: return

    if not self.is_admin and not request.method == 'GET':
      abort(403, message = 'Permission denied! Does not have enough permissions for access this resource')

    domain_name, service_name = kwargs.get('domain_name'), kwargs.get('service_name')
    if not domain_name:
      abort(400, message = 'Could not find "domain_name"')

    domain = abort_if_obj_doesnt_exist('name', domain_name, Domain)
    if self.request_user_reseller_id != domain.client.reseller_id:
      abort(403, message = 'Permission denied! The requester does not belong to the requested domain.')

    if self.request_user_client_id != domain.client_id and not self.is_admin:
      abort(403, message = 'Permission denied! The requester does not belong to the requested domain.')
    
class CosResource(BaseResource):
  def __init__(self):
    filters = ['id', 'name']
    super(CosResource, self).__init__(filters)

  def get(self, service_name, target_cos):
    service = abort_if_obj_doesnt_exist('name', service_name, Service)
    cos = abort_if_obj_doesnt_exist(self.filter_by, target_cos, Cos)
    hasmany = False
    if type(cos) == list:
      hasmany = True

    return { 'response' : CosSchema(many=False).dump(cos).data }

  def delete(self, service_name, target_cos):
    service = abort_if_obj_doesnt_exist('name', service_name, Service)
    cos = abort_if_obj_doesnt_exist(self.filter_by, target_cos, Cos)

    cred = service.credentials
    if not cred:
      abort(400, message = 'Could not find any credentials for the service %s' % service.name)
    admin_user, admin_password = cred.identity, cred.secret

    async_obj = delete_cos_zimbra_task.delay(
      target_cos = target_cos,
      zimbra_config = {
        'service_api' : service.service_api,
        'admin_user' : admin_user,
        'admin_password' : admin_password
      },
      cos_id = cos.id
    )
    return {
      'response' : {
        'task_id' : async_obj.id,
      }
    }, 202

  def put(self, service_name, target_cos):
    service = abort_if_obj_doesnt_exist('name', service_name, Service)
    cos = abort_if_obj_doesnt_exist(self.filter_by, target_cos, Cos)

    cred = service.credentials
    if not cred:
      abort(400, message = 'Could not find any credentials for the service %s' % service.name)
    admin_user, admin_password = cred.identity, cred.secret

    self.parser.add_argument('features', type=dict, required=True)
    reqdata = self.parser.parse_args()

    # It will create or append features to a COS based on the query result
    for feature, data in reqdata['features'].items():
      existent_feature = Features.query.filter_by(name=feature).first()
      if existent_feature:
        cos.features.append(existent_feature)
      else:
        cos.features.append(Features(name=feature, desc=data.get('desc') or '', value=data.get('value') or 'TRUE'))

    # Zimbra API CreateCosRequest COS features parameters
    features = {}
    for key, data in reqdata['features'].items():
      value = data.get('value') or 'TRUE'
      features[key] = value

    db.session.add(cos)
    db.session.commit()

    async_obj = modify_cos_zimbra_task.delay(
      target_cos = cos.name,
      zimbra_config = {
        'service_api' : service.service_api,
        'admin_user' : admin_user,
        'admin_password' : admin_password
      },
      features = features,
      sync_state_id = 0
    )

    return {
      'response' : {
        'task_id' : async_obj.id,
        'async_id' : 0
      }
    }, 202

  # TODO: RESTFUL tip - The pending request should be exposed as a resource so the client can check up on it later.
  # The Location header can contain the URI to this resource
  def post(self, service_name, target_cos):
    service = abort_if_obj_doesnt_exist('name', service_name, Service)

    cred = service.credentials
    if not cred:
      abort(400, message = 'Could not find any credentials for the service %s' % service.name)
    admin_user, admin_password = cred.identity, cred.secret

    self.parser.add_argument('features', type=dict, required=True)
    reqdata = self.parser.parse_args()

    # COS are unique to services
    for cos in Cos.query.filter_by(name=target_cos).all():
      if cos.service_id == service.id:
        abort(409, message='A COS with the name %s already belongs to service %s' % (target_cos, service_name))
        # TODO: the Location header should point to the URI of that resource: that is, the source of the conflict

    cos = Cos(name=target_cos)
    cos.service_id = service.id

    features = reqdata['features'].keys()

    # It will create or append features to a COS based on the query result
    for feature, data in reqdata['features'].items():
      existent_feature = Features.query.filter_by(name=feature).first()
      if existent_feature:
        cos.features.append(existent_feature)
      else:
        cos.features.append(Features(name=feature, desc=data.get('desc') or '', value=data.get('value') or 'TRUE'))

    # Zimbra API CreateCosRequest COS features parameters
    features = {}
    for key, data in reqdata['features'].items():
      value = data.get('value') or 'TRUE'
      features[key] = value

    db.session.add(cos)
    db.session.commit()

    async_obj = create_cos_zimbra_task.delay(
      target_cos = target_cos,
      zimbra_config = {
        'service_api' : service.service_api,
        'admin_user' : admin_user,
        'admin_password' : admin_password
      },
      features = features,
      cos_id = cos.id
    )

    return {
      'response' : {
        'cosid' : cos.id,
        'task_id' : async_obj.id,
        'async_id' : 0
      }
    }, 202

from flask_restful import abort, request
import json

from jenova.resources.base import BaseResource, abort_if_obj_doesnt_exist
from jenova.models import Domain, Service
from jenova.components import db, ZimbraRequest

class DistributionListsResource(BaseResource):
  def __init__(self):
    filters = ['id', 'name']
    super(DistributionListsResource, self).__init__(filters)

  @property
  def scope(self):
    return 'zimbra'

  # Overrided
  def is_forbidden(self, **kwargs):
    """ Check for access rules:
    A global admin must not have any restrictions.
    A requester must have access of your own domains (reseller) only if is an admin
    A requester must have access of your own domains (client) 
    """
    if self.is_global_admin: return

    
    domain_name, service_name = kwargs.get('domain_name'), kwargs.get('service_name')
    if not domain_name:
      abort(400, message = 'Could not find "domain_name"')

    domain = abort_if_obj_doesnt_exist('name', domain_name, Domain)
    if self.request_user_reseller_id != domain.client.reseller_id:
      abort(403, message = 'Permission denied! The requester does not belong to the requested domain.')

    if self.request_user_client_id != domain.client_id and not self.is_admin:
      abort(403, message = 'Permission denied! The requester does not belong to the requested domain.')

  def get(self, service_name, domain_name):    
    domain = abort_if_obj_doesnt_exist('name', domain_name, Domain)
    service = abort_if_obj_doesnt_exist('name', service_name, Service)
    
    cred = service.credentials
    if not cred:
      abort(400, message = 'Could not find any credentials for the service %s' % service.name)
    admin_user, admin_password = cred.identity, cred.secret
    
    zr = ZimbraRequest(
      admin_url = service.service_api, 
      admin_user = admin_user, 
      admin_pass = admin_password
    )

    r = zr.getAllDistributionList(domain_name=domain_name)

    res = {
      'dlists' : [],
      'total' : 0
    }

    if type(r['GetAllDistributionListsResponse']['dl']) is not list:
      r['GetAllDistributionListsResponse']['dl'] = [r['GetAllDistributionListsResponse']['dl']]
    for dlist in r['GetAllDistributionListsResponse']['dl']:
      data = dict()
      data['name'] = dlist['name']
      data['zimbraId'] = dlist['id']
      res['dlists'].append(data)
      res['total'] += 1
    
    return {'response' : res}

  def post(self, service_name, domain_name):
    domain = abort_if_obj_doesnt_exist('name', domain_name, Domain)
    service = abort_if_obj_doesnt_exist('name', service_name, Service)

    cred = service.credentials
    if not cred:
      abort(400, message = 'Could not find any credentials for the service %s' % service.name)
    admin_user, admin_password = cred.identity, cred.secret

    self.parser.add_argument('dlist', type=str, required=True, location='json')
    self.parser.add_argument('accounts', type=list, required=True, location='json')
    
    reqdata = self.parser.parse_args()
        
    dlist_name = reqdata['dlist']
    
    if not domain_name == dlist_name.split('@')[1]:
      abort(400, message = 'Distribution List must belong to required domain %s' % domain_name)    

    members = []
    for account in reqdata['accounts']:
      if not domain_name == account['name'].split('@')[1]:
        abort(400, message = 'Account must belong to required domain %s' % domain_name)
      members.append(account['name'])

    zr = ZimbraRequest(
      admin_url = service.service_api, 
      admin_user = admin_user, 
      admin_pass = admin_password
    )

    r = zr.createDistributionList(dlist=dlist_name)
    res = {
        'name' : "",
        'zimbraId' : "" 
    }
    
    res['name'] = r['CreateDistributionListResponse']['dl']['name']
    res['zimbraId'] = r['CreateDistributionListResponse']['dl']['id']
    
    res_members = zr.addDistributionListMember(res['zimbraId'],members)
    
    return {'response' : res}, 201

class DistributionListResource(BaseResource):
  def __init__(self):
    filters = ['id', 'name']
    super(DistributionListResource, self).__init__(filters)

  @property
  def scope(self):
    return 'zimbra'

  # Overrided
  def is_forbidden(self, **kwargs):
    """ Check for access rules:
    A global admin must not have any restrictions.
    A requester must have access of your own domains (reseller) only if is an admin
    A requester must have access of your own domains (client) 
    """
    if self.is_global_admin: return

    domain_name, service_name = kwargs.get('domain_name'), kwargs.get('service_name')
    if not domain_name:
      abort(400, message = 'Could not find "domain_name"')

    domain = abort_if_obj_doesnt_exist('name', domain_name, Domain)
    if self.request_user_reseller_id != domain.client.reseller_id:
      abort(403, message = 'Permission denied! The requester does not belong to the requested domain.')

    if self.request_user_client_id != domain.client_id and not self.is_admin:
      abort(403, message = 'Permission denied! The requester does not belong to the requested domain.')

  def get(self, service_name, domain_name, dlist_name):   
    domain = abort_if_obj_doesnt_exist('name', domain_name, Domain)
    service = abort_if_obj_doesnt_exist('name', service_name, Service)
    cred = service.credentials
    if not cred:
      abort(400, message = 'Could not find any credentials for the service %s' % service.name)
    admin_user, admin_password = cred.identity, cred.secret

    zr = ZimbraRequest(
      admin_url = service.service_api, 
      admin_user = admin_user, 
      admin_pass = admin_password
    )

    try:
      r = zr.getDistributionList(dlist_name)
    except Exception, e:
      return {'response': {
                'message': e.message
                }
              }, 404

    res = { 'dlist' : "",
            'accounts' : [],
            'total' : 0
          }

    res['dlist'] = dlist_name

    if not r['GetDistributionListResponse']['dl'].get('dlm'):
      return {'response' : res}

    for accounts in r['GetDistributionListResponse']['dl']['dlm']:
      data = dict()
      data['name'] = accounts['_content']
      res['accounts'].append(data)
      res['total'] += 1
    
    return {'response' : res}

  def put(self, service_name, domain_name, dlist_name):
    domain = abort_if_obj_doesnt_exist('name', domain_name, Domain)
    service = abort_if_obj_doesnt_exist('name', service_name, Service)

    cred = service.credentials
    if not cred:
      abort(400, message = 'Could not find any credentials for the service %s' % service.name)
    admin_user, admin_password = cred.identity, cred.secret

    username, domain = dlist_name.split('@')
    if not domain_name == domain:
      abort(400, message = 'Dlist must belong to required domain %s' % domain_name)

    self.parser.add_argument('dlist', type=str, required=True, location='json')
    self.parser.add_argument('accounts', type=list, required=True, location='json')
    
    reqdata = self.parser.parse_args()        
    dlist_name = dlist_name
    
    if not domain_name == dlist_name.split('@')[1]:
      abort(400, message = 'Distribution List must belong to required domain %s' % domain_name)    

    members = []
    current_members = []
    for account in reqdata['accounts']:
      if not domain_name == account['name'].split('@')[1]:
        abort(400, message = 'Account must belong to required domain %s' % domain_name)
      members.append(account['name'])

    zr = ZimbraRequest(
      admin_url = service.service_api, 
      admin_user = admin_user, 
      admin_pass = admin_password
    )

    r = zr.getDistributionList(dlist_name) 

    if r['GetDistributionListResponse']['dl'].get('dlm'):
      if type(r['GetDistributionListResponse']['dl']['dlm']) is not list:
        r['GetDistributionListResponse']['dl']['dlm'] = [r['GetDistributionListResponse']['dl']['dlm']]

      for accounts in r['GetDistributionListResponse']['dl']['dlm']:
        current_members.append(accounts['_content'])

    membersToAdd = list(set(members) - set(current_members))
    membersToRem = list(set(current_members) - set(members))

    idDList = zr.getDistributionListId(dlist_name)

    if membersToAdd: 
      zr.addDistributionListMember(idDList,membersToAdd)
    
    if membersToRem:
      zr.removeDistributionListMember(idDList,membersToRem)

    return {'response': 'DList update sucessful'}, 201

  def delete(self, service_name, domain_name, dlist_name):
    domain = abort_if_obj_doesnt_exist('name', domain_name, Domain)
    service = abort_if_obj_doesnt_exist('name', service_name, Service)

    cred = service.credentials
    if not cred:
      abort(400, message = 'Could not find any credentials for the service %s' % service.name)
    admin_user, admin_password = cred.identity, cred.secret

    username, domain = dlist_name.split('@')
    if not domain_name == domain:
      abort(400, message = 'Account must belong to required domain %s' % domain_name)

    zr = ZimbraRequest(
      admin_url = service.service_api, 
      admin_user = admin_user, 
      admin_pass = admin_password
    )

    zid = zr.getDistributionListId(dlist_name=dlist_name)
    zr.deleteDistributionList(dlist_zimbra_id = zid)
    return {}, 204
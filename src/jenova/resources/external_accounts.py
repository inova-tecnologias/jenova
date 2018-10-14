from flask_restful import abort, request
import json, datetime

from jenova.resources.base import BaseResource, abort_if_obj_doesnt_exist
from jenova.models import Domain, Service
from jenova.components import db, ZimbraRequest

ZIMBRA_SUPPORTED_ATTRIBUTES = ['givenName', 'sn', 'displayName', 'zimbraAccountStatus', 'zimbraId', 'zimbraCOSId', 'zimbraMailAlias']
IGNORE_BODY_ATTRIBUTES = ['zimbraId', 'userPassword', 'zimbraMailAlias', 'addZimbraMailAlias', 'removeZimbraMailAlias']
class ExternalDomainStatusResource(BaseResource):
  def __init__(self):
    filters = ['id', 'name']
    super(ExternalDomainStatusResource, self).__init__(filters)
  
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

    r = zr.getDomain(domain=domain_name, attrs=['zimbraDomainStatus'])
    rdata = { 
      'status' : r['GetDomainResponse']['domain']['a'][0]['_content'],
      'name' : r['GetDomainResponse']['domain']['name'],
      'id' : r['GetDomainResponse']['domain']['id'],
    }
    return {'response' : rdata}
  
  def put(self, service_name, domain_name):
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

    self.parser.add_argument('status', type = str)
    reqdata = self.parser.parse_args()

    domain_id = zr.getDomainId(domain_name)
    zr.modifyDomain(domain_id=domain_id, attrs=[('zimbraDomainStatus', reqdata['status'])])
    
    return ''
  

class ExternalAccountsListResource(BaseResource):
  def __init__(self):
    filters = ['id', 'name']
    super(ExternalAccountsListResource, self).__init__(filters)
  
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


    self.parser.add_argument('limit', type=int, location='args')
    self.parser.add_argument('offset', type=int, location='args')
    reqdata = self.parser.parse_args()
    offset, limit = reqdata.get('offset') or 0, reqdata.get('limit') or 100

    zr = ZimbraRequest(
      admin_url = service.service_api, 
      admin_user = admin_user, 
      admin_pass = admin_password
    )

    r = zr.searchDirectory(
      domain_name = domain_name,
      types = 'accounts',
      attrs = ','.join(ZIMBRA_SUPPORTED_ATTRIBUTES),
      query = '(objectClass=zimbraAccount)(mail=*)',
      limit = limit,
      offset = offset
    )

    res = {
      'accounts' : [],
      'total' : 0
    }

    if r['SearchDirectoryResponse']['searchTotal'] == 0:
      return { 'response' : res  }
    if type(r['SearchDirectoryResponse']['account']) is not list:
      r['SearchDirectoryResponse']['account'] = [r['SearchDirectoryResponse']['account']]
    for account in r['SearchDirectoryResponse']['account']:
      data = dict()
      data['name'] = account['name']
      for attribute in account['a']:
        if data.get(attribute['n']):
          if type(data[attribute['n']]) is not list:
            data[attribute['n']] = [data[attribute['n']]]
          data[attribute['n']].append(attribute['_content'])
        else:
          if attribute['n'] == 'zimbraMailAlias':
            data[attribute['n']] = [attribute['_content']]
          else:
            data[attribute['n']] = attribute['_content']
      res['accounts'].append(data)
      res['total'] += 1

    return { 'response' : res  }

class ExternalAccountsResource(BaseResource):
  def __init__(self):
    filters = ['id', 'name']
    super(ExternalAccountsResource, self).__init__(filters)

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

  def get(self, service_name, domain_name, target_account):
    domain = abort_if_obj_doesnt_exist('name', domain_name, Domain)
    service = abort_if_obj_doesnt_exist('name', service_name, Service)
    cred = service.credentials
    if not cred:
      abort(400, message = 'Could not find any credentials for the service %s' % service.name)
    admin_user, admin_password = cred.identity, cred.secret

    self.parser.add_argument('limit', type=int, location='args')
    self.parser.add_argument('offset', type=int, location='args')
    reqdata = self.parser.parse_args()
    offset, limit = reqdata.get('offset') or 0, reqdata.get('limi') or 100

    zr = ZimbraRequest(
      admin_url = service.service_api, 
      admin_user = admin_user, 
      admin_pass = admin_password
    )

    r = zr.searchDirectory(
      domain_name = domain_name,
      types = 'accounts',
      attrs = ','.join(ZIMBRA_SUPPORTED_ATTRIBUTES),
      query = '(mail=*%s*)' % target_account,
      limit = limit,
      offset = offset 
    )

    res = []
    if r['SearchDirectoryResponse']['searchTotal'] == 0:
      abort(404, message = 'Could not find any domain')
    if type(r['SearchDirectoryResponse']['account']) is not list:
      r['SearchDirectoryResponse']['account'] = [r['SearchDirectoryResponse']['account']]
    for account in r['SearchDirectoryResponse']['account']:
      data = dict()
      data['name'] = account['name']
      for attribute in account['a']:
        if data.get(attribute['n']):
          if type(data[attribute['n']]) is not list:
            data[attribute['n']] = [data[attribute['n']]]
          data[attribute['n']].append(attribute['_content'])
        else:
          if attribute['n'] == 'zimbraMailAlias':
            data[attribute['n']] = [attribute['_content']]
          else:
            data[attribute['n']] = attribute['_content']
      res.append(data)

    return { 'response' : res  }

  def put(self, service_name, domain_name, target_account):
    domain = abort_if_obj_doesnt_exist('name', domain_name, Domain)
    service = abort_if_obj_doesnt_exist('name', service_name, Service)

    cred = service.credentials
    if not cred:
      abort(400, message = 'Could not find any credentials for the service %s' % service.name)
    admin_user, admin_password = cred.identity, cred.secret

    if not domain_name == target_account.split('@')[1]:
      abort(400, message = 'Account must belong to required domain %s' % domain_name)
    zr = ZimbraRequest(
      admin_url = service.service_api, 
      admin_user = admin_user, 
      admin_pass = admin_password
    )

    for zattr in ZIMBRA_SUPPORTED_ATTRIBUTES:
      self.parser.add_argument(zattr, type = str, default = '')

    self.parser.add_argument('userPassword', type = str)
    self.parser.add_argument('addZimbraMailAlias', action="append")
    self.parser.add_argument('removeZimbraMailAlias', action="append")
    reqdata = self.parser.parse_args()

    if reqdata.get('userPassword'):
      self.logger.info('reseting password for account')
      zr.setPassword(account_zimbra_id=reqdata['zimbraId'], password=reqdata['userPassword'])

    if reqdata.get('addZimbraMailAlias'):
      for alias in reqdata.get('addZimbraMailAlias'):
        d = abort_if_obj_doesnt_exist('name', alias.split('@')[1], Domain)
        if not self.is_global_admin:
          if self.request_user_reseller_id != d.client.reseller_id:
            abort(403, message = 'Permission denied! The requester does not belong to the requested domain.')
          if self.request_user_client_id != d.client_id and not self.is_admin:
            abort(403, message = 'Permission denied! The requester does not belong to the requested domain.')
        zr.addAccountAlias(zid=reqdata['zimbraId'], alias=alias)
      
    if reqdata.get('removeZimbraMailAlias'):
      for alias in reqdata.get('removeZimbraMailAlias'):
        zr.removeAccountAlias(zid=reqdata['zimbraId'], alias=alias)

    modify_attrs = []
    for k, v in reqdata.iteritems():
      if k in IGNORE_BODY_ATTRIBUTES:
        continue
      modify_attrs.append([k, v])

    zr.modifyAccount(account_zimbra_id = reqdata['zimbraId'],
                      attrs=modify_attrs)
    return ''
  
  def post(self, service_name, domain_name, target_account):
    domain = abort_if_obj_doesnt_exist('name', domain_name, Domain)
    service = abort_if_obj_doesnt_exist('name', service_name, Service)

    cred = service.credentials
    if not cred:
      abort(400, message = 'Could not find any credentials for the service %s' % service.name)
    admin_user, admin_password = cred.identity, cred.secret

    if not domain_name == target_account.split('@')[1]:
      abort(400, message = 'Account must belong to required domain %s' % domain_name)
    zr = ZimbraRequest(
      admin_url = service.service_api, 
      admin_user = admin_user, 
      admin_pass = admin_password
    )

    for zattr in ZIMBRA_SUPPORTED_ATTRIBUTES:
      self.parser.add_argument(zattr, type = str, default = '')

    self.parser.add_argument('userPassword', type = str)
    reqdata = self.parser.parse_args()

    zattrs = []
    for k, v in reqdata.iteritems():
      if k in IGNORE_BODY_ATTRIBUTES:
        continue
      zattrs.append([k, v])

    res = zr.createAccount(account = target_account,
                      attrs=zattrs,
                      password=reqdata['userPassword'])
    return {
      'response' : {
        'name' : res['CreateAccountResponse']['account']['name'],
        'id' : res['CreateAccountResponse']['account']['id']
      }
    }, 201

  def delete(self, service_name, domain_name, target_account):
    domain = abort_if_obj_doesnt_exist('name', domain_name, Domain)
    service = abort_if_obj_doesnt_exist('name', service_name, Service)

    cred = service.credentials
    if not cred:
      abort(400, message = 'Could not find any credentials for the service %s' % service.name)
    admin_user, admin_password = cred.identity, cred.secret

    username, domain = target_account.split('@')
    if not domain_name == domain:
      abort(400, message = 'Account must belong to required domain %s' % domain_name)

    zr = ZimbraRequest(
      admin_url = service.service_api, 
      admin_user = admin_user, 
      admin_pass = admin_password
    )

    zid = zr.getAccountId(account_name=target_account)
    now = datetime.datetime.now()
    date = now.strftime("%Y%m%d%H%M%S")
    new_name = '%s__%s__%s@deleted.accounts' % (date, username, domain)
    self.logger.info('delete account: renaming account %s to %s' % (
                      target_account, new_name
    ))
    
    r = zr.getAccountMembership(account=zid, by="id")
    if r['GetAccountMembershipResponse'].get('dl'):
      if type(r['GetAccountMembershipResponse']['dl']) is not list:
        r['GetAccountMembershipResponse']['dl'] = [r['GetAccountMembershipResponse']['dl']]
      for dl in r['GetAccountMembershipResponse']['dl']:
        self.logger.info('delete account: removing member %s from %s' % 
                          (target_account, dl['name']))
        zr.removeDistributionListMember(dlist_zimbra_id=dl['id'], members=[target_account])
    
    zr.renameAccount(zid = zid, new_name = new_name)
    return {}, 204
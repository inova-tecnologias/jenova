"""
General information about domains and services
"""

from sqlalchemy import distinct
from flask.ext.restful import abort, Resource, reqparse
from flask import redirect, request
from datetime import datetime
from celery import chain
from sqlalchemy.orm.exc import NoResultFound
from redis import StrictRedis
import hmac, hashlib, time, uuid, re, requests

from jenova.resources.base import BaseResource, abort_if_obj_doesnt_exist
from jenova.components import db, ZimbraRequest, ZimbraRequestError, Config, PowerDns, Mxhero, DnsError
from jenova.models import (
  Client, Domain, DomainServiceState, Service, Cos,
  DomainServiceStateSchema, DomainSchema, Reseller, ResellerServices, DnsRecordBackup
)
from jenova.components.tasks import (
  update_cos_into_domain_zimbra_task, create_domain_zimbra_task,
  delete_domain_zimbra_task, create_delegated_zimbra_admin_task
)

class DomainListByQueryResource(BaseResource):
  def __init__(self):
    filters = ['name']
    super(DomainListByQueryResource, self).__init__(filters)

  @property
  def scope(self):
    return 'domain'

  # Overrided
  def is_forbidden(self, **kwargs):
    """ Check for access rules:
    A global admin must not have any restrictions.
    A requester must have access of your own domains (reseller) only if is an admin
    A requester must have access of your own domains (client)
    """
    if self.is_global_admin: return

    client_name, reseller_name = kwargs.get('client_name'), kwargs.get('reseller_name')
    if client_name:
      client = abort_if_obj_doesnt_exist('name', client_name, Client)
      if self.request_user_reseller_id != client.reseller_id:
        abort(403, message = 'Permission denied! The requester could not access this client')
      if self.request_user_client_id != client.id and not self.is_admin:
        abort(403, message = 'Permission denied! Tried to query a restricted client')
    elif reseller_name:
      reseller = abort_if_obj_doesnt_exist('name', reseller_name, Reseller)
      if self.request_user_reseller_id != reseller.id:
        abort(403, message = 'Permission denied! The requester could not access this reseller')
    else:
      abort(403, message = 'Permission denied! Could not query for all domains')

  def get(self, by_name_query, client_name='', reseller_name=''):
    self.parser.add_argument('limit', type=int, location='args')
    self.parser.add_argument('offset', type=int, location='args')
    reqdata = self.parser.parse_args()
    offset, limit = reqdata.get('offset') or 0, reqdata.get('limit') or 100
    
    if client_name:
      total_records = Domain.query.join(Client, Client.id == Domain.client_id) \
        .filter(Client.name == client_name) \
        .filter(Domain.name.like('%' + by_name_query + '%'))\
        .count()

      domains = Domain.query.join(Client, Client.id == Domain.client_id) \
        .filter(Domain.name.like('%' + by_name_query + '%'))\
        .filter(Client.name == client_name) \
        .offset(offset)\
        .limit(limit)\
        .all()
    elif reseller_name:
      reseller = abort_if_obj_doesnt_exist(self.filter_by, reseller_name, Reseller)

      total_records = Domain.query\
        .filter(Reseller.id == Client.reseller_id)\
        .filter(Domain.client_id == Client.id)\
        .filter(Reseller.id == reseller.id)\
        .filter(Domain.name.like('%' + by_name_query + '%'))\
        .count()

      domains = Domain.query\
        .filter(Reseller.id == Client.reseller_id)\
        .filter(Domain.client_id == Client.id)\
        .filter(Reseller.id == reseller.id)\
        .filter(Domain.name.like('%' + by_name_query + '%'))\
        .offset(offset)\
        .limit(limit)\
        .all()
    else:
      total_records = Domain.query\
        .filter(Domain.name.like('%' + by_name_query + '%'))\
        .count()

      domains = Domain.query\
        .filter(Domain.name.like('%' + by_name_query + '%'))\
        .offset(offset)\
        .limit(limit)\
        .all()


    if total_records == 0:
      return {
        'response' : {
          'domains' : [],
          'message' : 'Could not find any domain using query: %s' % by_name_query,
          'total' : total_records
        }
      }, 404


    return {
      'response' : {
        'domains' : DomainSchema(many=True).dump(domains).data,
        'total' : total_records
      }
    }, 200

class DomainListServiceStateResource(BaseResource):
  def __init__(self):
    filters = ['id', 'name']
    super(DomainListServiceStateResource, self).__init__(filters)

  @property
  def scope(self):
    return 'domain'

  # Overrided
  def is_forbidden(self, client_name, target_domain):
    """ Check for access rules:
    A global admin must not have any restrictions.
    A requester must have access of your own domains (reseller) only if is an admin
    A requester must have access of your own domains (client)
    """
    if self.is_global_admin: return
    if not client_name:
      abort(400, message = 'Could not find "client_name"')
    client = abort_if_obj_doesnt_exist('name', client_name, Client)

    if self.request_user_reseller_id != client.reseller_id:
      abort(403, message = 'Permission denied! Client does not belong to the request user reseller.')

    if self.request_user_client_id != client.id and not self.is_admin:
      abort(403, message = 'Permission denied! Client does not belong to the requested user.')

  def get(self, client_name, target_domain):
    domain = Domain.query.join(Client, Client.id == Domain.client_id) \
      .filter(Domain.name == target_domain) \
      .filter(Client.name == client_name) \
      .first()

    if not domain:
      abort(404, message = 'Could not find domain %s of client %s' % (client_name, target_domain))

    # Must NOT has more than one domain for a client!
    domain_service_states = DomainServiceState.query \
      .filter_by(domain_id = domain.id) \
      .all()

    # global admins gets all services
    if self.is_global_admin or self.is_admin:
      if self.is_global_admin:
        services = Service.query.all()
      else:
        services = Service.query.join(ResellerServices, ResellerServices.service_id == Service.id) \
        .join(Client, Client.reseller_id == ResellerServices.reseller_id) \
        .join(Domain, Domain.client_id == Client.id) \
        .filter(Domain.name == target_domain) \
        .all()
      # load services allowed for resellers activate.
      if services:
        for service in services:
          service_exist = False
          for dss in domain_service_states:
            if service.id == dss.service_id:
              service_exist = True;
          if not service_exist:
            new_service_state = DomainServiceState(
              domain = domain,
              service = service,
              enabled = False
            )
            domain_service_states.append(new_service_state)

    if not domain_service_states:
      abort(404, message = 'Could not find any service. Client: %s Domain: %s' % (client_name, target_domain))

    return {
      'response' : {
        'domains' : {
          'name' : domain.name,
          'states' : DomainServiceStateSchema(many=True).dump(domain_service_states).data
        }
      }
    }

class DomainServiceStateResource(BaseResource):
  def __init__(self):
    filters = ['id', 'name']
    super(DomainServiceStateResource, self).__init__(filters)

  @property
  def scope(self):
    return 'domain'

  # Global Admin endpoint
  def put(self, client_name, target_domain, service_name):
    self.parser.add_argument('enable', type=bool, required=True)
    reqdata = self.parser.parse_args()

    state = reqdata.get('enable')

    domain = Domain.query.join(Client, Client.id == Domain.client_id) \
      .filter(Domain.name == target_domain) \
      .filter(Client.name == client_name) \
      .one()

    if not domain:
      abort(404, message = 'Could not find domain %s of client %s' % (client_name, target_domain))

    service = abort_if_obj_doesnt_exist('name', service_name, Service)
    

    try:
      # Must NOT has more than one domain for a client!
      domstate = DomainServiceState.query \
        .filter(DomainServiceState.domain_id == domain.id) \
        .filter(DomainServiceState.service_id == service.id) \
        .one()
        
      
    except NoResultFound:
      abort(400, message = 'Could not find any state. Check if the service is enabled for this domain.')

    if state is not None:
      domstate.enabled = state
    db.session.commit()

    return '', 204

class DomainListResource(BaseResource):
  def __init__(self):
    filters = ['id', 'name']
    super(DomainListResource, self).__init__(filters)

  @property
  def scope(self):
    return 'domain'

  # Overrided
  def is_forbidden(self, client_name):
    """ Check for access rules:
    A global admin must not have any restrictions.
    A requester must have access of your own domains (reseller) only if is an admin
    A requester must have access of your own domains (client) 
    """
    if self.is_global_admin: return

    client = abort_if_obj_doesnt_exist('name', client_name, Client)
    if self.request_user_reseller_id != client.reseller_id:
      abort(403, message = 'Permission denied! The client does not belong to the requested user reseller')

    if self.request_user_client_id != client.id and not self.is_admin:
      abort(403, message = 'Permission denied! The client does not belong to the requested user')

  def get(self, client_name):
    self.parser.add_argument('limit', type=int, location='args')
    self.parser.add_argument('offset', type=int, location='args')
    reqdata = self.parser.parse_args()
    offset, limit = reqdata.get('offset') or 0, reqdata.get('limit') or 25

    client = abort_if_obj_doesnt_exist('name', client_name, Client)
    count = client.domain.count()
    
    domains = client.domain\
      .offset(offset)\
      .limit(limit)\
      .all()

    if not domains:
      abort(404, message = 'Could not find any domains')
    return {
      'response' : {
        'domains' : DomainSchema(many=True).dump(domains).data,
        'total' : count
      }
    }

class DomainResource(BaseResource):
  """ Domain Resource represents only a record in jenovadb, it doesn't have any physical 
  association with external services. A physical association means that a domain has a promisse 
  with external services, this kind of association is represented by others resources.
  A domain is unique to clients, several clients could have the same domain created.
  A domain is unique to services, a domain cannot be associate with the same service.
  """
  def __init__(self):
    filters = ['id', 'name']
    super(DomainResource, self).__init__(filters)

  @property
  def scope(self):
    return 'domain'

  # Overrided
  def is_forbidden(self, client_name, domain_name):
    """ Check for access rules:
    A global admin must not have any restrictions.
    A requester must have access of your own domains (reseller) only if is an admin
    A requester must have access of your own domains (client) 
    """
    if self.is_global_admin: return
    if not client_name:
      abort(400, message = 'Could not find "client_name"')

    client = abort_if_obj_doesnt_exist('name', client_name, Client)
    if self.request_user_reseller_id != client.reseller_id:
      abort(403, message = 'Permission denied! The client does not belong to the requested user reseller')

    if self.request_user_client_id != client.id and not self.is_admin:
      abort(403, message = 'Permission denied! The client does not belongs to the requested user')

  def get(self, client_name, domain_name):
    domain = Domain.query.join(Client, Client.id == Domain.client_id) \
      .filter(Domain.name == domain_name) \
      .filter(Client.name == client_name).first()
    reqdata = self.parser.parse_args()

    if not domain:
      abort(404, message = 'Could not find domain "%s" of client "%s"' % (domain_name, client_name))

    return DomainSchema().dump(domain).data

  def delete(self, client_name, domain_name):
    domain = Domain.query.join(Client, Client.id == Domain.client_id) \
      .filter(Domain.name == domain_name) \
      .filter(Client.name == client_name).first()

    self.parser.add_argument('force', type=int, location='args', default=0)
    reqdata = self.parser.parse_args()
    
    if not domain:
      abort(404, message = 'Could not find domain "%s" of client "%s"' % (domain_name, client_name))

    if domain.services and not reqdata['force']:
      # TODO: Set location header of conflicts
      abort(409, message = 'The domain still has services bound to it')
      
      # LOG WARNING!!!
      # Force only if you known what are you doing!!
      
    db.session.delete(domain)
    db.session.commit()
    return '', 204

  def post(self, client_name, domain_name):
    client = abort_if_obj_doesnt_exist('name', client_name, Client)

    self.parser.add_argument('services', type=str, action='append')
    reqdata = self.parser.parse_args(strict=True)
    
    domain_name = domain_name.lower()
    # A domain is unique to a client
    if Domain.query.join(Client, Client.id == Domain.client_id) \
      .filter(Domain.name == domain_name) \
      .filter(Client.name == client_name).first():
      abort(409, message='The domain "{}" already exists for this client "{}"'.format(domain_name, client_name))

    domain = Domain(
      name = domain_name,
      client_id = client.id
    )

    # On creation it's possible to append services to the domain - global admin
    if reqdata.get('services'):
      if not self.is_global_admin:
        abort(403, message = 'Permission denied! Does not have enough permissions.')
        
      for service_name in set(reqdata['services']):
        service = Service.query.filter_by(name = service_name).first()
        if not service:
          db.session.rollback()
          abort(404, message = 'Could not find service: %s' % service)

        # This is important! One service MUST be bound to ONE domain only
        if Domain.query \
          .filter(Domain.name == domain_name) \
          .filter(Domain.services.any(name = service.name)).first():
          # TODO: set the Location of the conflict
          db.session.rollback()
          abort(409, message='A domain is unique to only one service!'.format(domain_name, service.name))

        domain.services.append(service)
        #services.append(service)
    db.session.add(domain)
    db.session.commit()
    #domain.syncstate = SyncState(domain_id = domain.id)
    domain = Domain.query.filter_by(name=domain_name).first()

    return { 
      'response' : {
        'domain_id' : domain.id
      } 
    }, 201

# This should be base for every linking all external services!
class DomainServiceResource(BaseResource):
  """ Links domains with external services. Some services have promises with a jenova domain.
  """
  def __init__(self, **kwargs):
    filters = ['name']
    self.zgconfig = kwargs['main_config']
    if not self.zgconfig['dlist'].get('grants'):
      abort(500, message = 'Could not find any grants in config.yaml')
    super(DomainServiceResource, self).__init__(filters)

  @property
  def scope(self):
    return 'domain'

  # Overrided
  def is_forbidden(self, service_name, domain_name):
    """ Check for access rules:
    A global admin must not have any restrictions.
    A requester must have access of your own domains (reseller) only if is an admin
    A requester must have access of your own domains (client) 
    """
    if self.is_global_admin: return
    if not domain_name:
      abort(400, message = 'Could not find "domain_name"')

    domain = abort_if_obj_doesnt_exist('name', domain_name, Domain)
    if self.request_user_reseller_id != domain.client.reseller_id:
      abort(403, message = 'Permission denied! The requester does not belong to the requested domain.')

    if self.request_user_client_id != domain.client_id and not self.is_admin:
      abort(403, message = 'Permission denied! The requester does not belong to the requested domain.')

  def delete(self, service_name, domain_name):
    domain = abort_if_obj_doesnt_exist('name', domain_name, Domain)
    service = abort_if_obj_doesnt_exist('name', service_name, Service)
    self.parser.add_argument('sync', type=str, location='args', choices=('0', '1'))
    self.parser.add_argument('force', type=int, location='args', default=0, choices=(0, 1))

    reqdata = self.parser.parse_args()
    force = reqdata.get('force') and True
    # print(force)
    synchronous = reqdata.get('sync') and True
    job_id, status_code = None, 200

    if service.service_type == 'DNS':
      if not service.credentials:
        abort(400, message = 'Could not find any credentials for the service %s' % service.name)
      cred = service.credentials
      try:
        pdns = PowerDns()  
        pdns.config(pdns_server = service.service_host, api_key = cred.secret)
        
        if force:
          res = pdns.get_domain(domain_name)
          compressed_records = res.json()['records']

          backup = DnsRecordBackup(
            domain = domain_name,
            records = compressed_records
          )

          self.logger.debug('backuping up zone: %s' % domain_name)

          db.session.add(backup)
          db.session.commit()

        pdns.delete_domain(domain_name = domain_name, force = force)
      except DnsError, e:
        if not e.status_code == 404:
          abort(e.status_code, message = e.message)
        # Domain does not exist in external service. Move on!
        # TODO: Emit WARNING for this condition

      domstate = DomainServiceState.query \
          .filter(DomainServiceState.domain_id == domain.id) \
          .filter(DomainServiceState.service_id == service.id) \
          .one()
      db.session.delete(domstate)
      db.session.commit()
      return '', 204
    elif service.service_type == 'MXHERO':
      try:
        mxh = Mxhero(environment=service.name, mxh_api=service.service_api)
        mxh.delete(domain_name)

        domstate = DomainServiceState.query \
            .filter(DomainServiceState.domain_id == domain.id) \
            .filter(DomainServiceState.service_id == service.id) \
            .one()
        db.session.delete(domstate)
        db.session.commit()
        return '', 204
      except Exception, e:
        abort(400, message = '%s' % e)
    elif service.service_type == 'ZIMBRA':
      if not service.credentials:
        abort(400, message = 'Could not find any credentials for the service %s' % service.name)
      cred = service.credentials
      admin_user, admin_password = cred.identity, cred.secret
      
      if synchronous:
        task_obj = delete_domain_zimbra_task.apply
      else:
        task_obj = delete_domain_zimbra_task.apply_async

      params = { 
        'zimbra_config' : {
          'service_api' : service.service_api,
          'admin_user' : admin_user,
          'admin_password' : admin_password
        },
        'domain_id' : domain.id,
        'service_id' : service.id,
        'domain_name' : domain_name
      }
      # Executes method apply (synchronous) or apply_async (asynchronous)
      result_task = task_obj(kwargs = params)
      if synchronous:
        if result_task.state == 'FAILURE':
          abort(400, message = 'Error completing task: %s' % result_task.result)
        status_code = 200
        job_id = result_task.id
      else:
        job_id = result_task.id
        status_code = 202
    else:
      abort(400, message = 'Not defined yet %s' % service.service_type)

    return { 
      'response' : {
        'task_id' : job_id or '',
      } 
    }, status_code

  # TODO: accept cos attributes in domain creation
  def put(self, service_name, domain_name):
    domain = abort_if_obj_doesnt_exist('name', domain_name, Domain)
    service = abort_if_obj_doesnt_exist('name', service_name, Service)
    status_code = 200
    job_parent_id, job_id = None, None

    if service.service_type == 'DNS':
      if not service.credentials:
        abort(400, message = 'Could not find any credentials for the service %s' % service.name)

      try:
        pdns = PowerDns()
        cred = service.credentials
        pdns.config(pdns_server = service.service_host, api_key = cred.secret)
        
        domstate = DomainServiceState.query \
          .filter(DomainServiceState.domain_id == domain.id) \
          .filter(DomainServiceState.service_id == service.id) \
          .first()

        if not domstate:
          domstate = DomainServiceState(
            domain = domain,
            service = service,
            enabled = True
          )
          db.session.add(domstate)
        else:
          domstate.enabled = True
        pdns.create_domain(domain_name)
      except DnsError, e:
        # 409 - Domain already exists. Skip and only associate the domain
        if not e.status_code == 409:
          db.session.rollback()
          abort(e.status_code, message = e.message)
        # TODO: Emit warning because the domain already exists in powerdns
      
      db.session.commit()
      return '', 204

    elif service.service_type == 'MXHERO':
      try:
        mxh = Mxhero(environment=service.name,mxh_api=service.service_api)
        res = mxh.create(domain_name).json()
        
        domstate = DomainServiceState.query \
          .filter(DomainServiceState.domain_id == domain.id) \
          .filter(DomainServiceState.service_id == service.id) \
          .first()

        if not domstate:
          domstate = DomainServiceState(
            domain = domain,
            service = service,
            enabled = True
          )
          db.session.add(domstate)
        else:
          domstate.enabled = True
        
        db.session.commit()
        return {
          'response' : res
        }, status_code
      except Exception, e:
        db.session.rollback()
        abort(400, message = e)
      db.session.commit()
      return '', 204
    elif service.service_type == 'ZIMBRA':
      # {'cos' : { 'zimbracos01' : quota, 'zimbracos02' : quota, (...) } }
      self.parser.add_argument('cos', type = dict)
      self.parser.add_argument('delegated_admin_account', type = str)
      reqdata = self.parser.parse_args()

      delegated_admin_account = reqdata['delegated_admin_account']
      if delegated_admin_account and not re.findall('@' + domain_name, delegated_admin_account):
        abort(400, message = 'The account domain (%s) must be the same as the domain (%s)' % \
          (delegated_admin_account, domain_name))
        
      request_cos = {}
      if reqdata.get('cos'):
        # Cos need to be registered in jenova database
        cos_names = reqdata['cos'].keys()
        all_cos = Cos.query.filter(Cos.name.in_(cos_names)).all()
        if not len(all_cos) == len(cos_names):
          abort(400, message = 'Could not find all registries. Register COS first.')
        
        # { 'zimbra-cos-id' : quota, ...}
        for cos in all_cos:
          try:
            # It will not raise KeyError because all cos'es are found
            quota = reqdata['cos'][cos.name]
            if not cos.zimbra_id:
              abort(400, message = 'Could not find zimbra_id for COS in Jenova DB.')
            request_cos[cos.zimbra_id] = int(quota)
          except ValueError:
            abort(400, 
              message = 'Wrong type found for quota: %s. Accept only integer. COS: %s' % (type(quota), cos.name)
            )

      cred = service.credentials
      if not cred:
        abort(400, message = 'Could not find any credentials for the service %s' % service.name)
      admin_user, admin_password = cred.identity, cred.secret
      
      target_dlist = '@'.join((self.zgconfig['dlist']['admin_list_name'], domain_name))
      zimbra_grants = Config.gen_zimbra_grants(
        zgrants = self.zgconfig['dlist']['grants'], 
        target_name = domain_name, 
        target_dlist = target_dlist
      )

      # Get the state of the domain
      domstate = DomainServiceState.query \
          .filter(DomainServiceState.domain_id == domain.id) \
          .filter(DomainServiceState.service_id == service.id) \
          .first()

      if not domstate:
        domstate = DomainServiceState(
          domain = domain,
          service = service,
          enabled = False
        )
        db.session.add(domstate)
        db.session.commit()

      # Start Task
      async_job = chain(
        create_domain_zimbra_task.s(
          zimbra_config = {
            'service_api' : service.service_api,
            'admin_user' : admin_user,
            'admin_password' : admin_password
          },
          domain_name = domain_name,
          domain_id = domain.id,
          quota = request_cos or None
        ),
        create_delegated_zimbra_admin_task.s(
          zimbra_config = {
            'service_api' : service.service_api,
            'admin_user' : admin_user,
            'admin_password' : admin_password
          },
          domain_name = domain_name,
          service_id = service.id,
          domain_id = domain.id,
          gconf = self.zgconfig,
          zgrants = zimbra_grants,
          delegated_admin_account = delegated_admin_account or None
        )
      )
      # Start JOB's
      job = async_job()
      job_parent_id, job_id = job.parent.id, job.id
      status_code = 202
    else:
      abort(400, message = 'Not defined yet %s' % service.service_type)

    return { 
      'response' : {
        'tasks_id' : [job_parent_id, job_id],
      } 
    }, status_code

# Generate preauth_key hmac.new(str(uuid.uuid4), str(uuid.uuid4()), hashlib.sha256).hexdigest() 
# ModifyDomainRequest { "n" : "zimbraPreauthKey", '_content' : hash_256 }
# TODO: Get preauth_key from ZIMBRA
class DomainServicePreAuthDelegationResource(BaseResource):
  def __init__(self, **kwargs):
    filters = ['name']
    self.zconfig = kwargs['main_config']
    if not self.zconfig['dlist'].get('grants'):
      abort(500, message = 'Could not find any grants in config.yaml')
    super(DomainServicePreAuthDelegationResource, self).__init__(filters)
    # Defaults to zimbraAdminAuthTokenLifetime expire time
    self.token_expires = 0
    self.redis = StrictRedis(kwargs['main_config']['redishost'])

  @property
  def scope(self):
    return 'zimbra'

  # Overrided
  def is_forbidden(self, service_name, domain_name):
    """ Check for access rules:
    A global admin must not have any restrictions.
    A requester must have access of your own domains (reseller) only if is an admin
    A requester must have access of your own domains (client) 
    """
    if self.is_global_admin: return
    if not domain_name:
      abort(400, message = 'Could not find "domain_name"')

    domain = abort_if_obj_doesnt_exist('name', domain_name, Domain)
    if self.request_user_reseller_id != domain.client.reseller_id:
      abort(403, message = 'Permission denied! The requester does not belong to the requested domain.')

    if self.request_user_client_id != domain.client_id and not self.is_admin:
      abort(403, message = 'Permission denied! The requester does not belong to the requested domain.')

  def _get_preauth_url(self, service_host, account_name, preauth_key):
    now_in_miliseconds = int(round(time.time() * 1000))
    msg = '%s|1|name|%s|%s' % (account_name, self.token_expires, now_in_miliseconds)
    
    preauth_computed = hmac.new(key = preauth_key, msg = msg, digestmod=hashlib.sha1).hexdigest()
    return 'https://%s:7071/service/preauth?account=%s&expires=%s&timestamp=%s&preauth=%s&admin=1' % \
    (service_host, account_name, self.token_expires, now_in_miliseconds, preauth_computed)

  def _set_account_perms(self, z_request, account_name, domain_name):
    account_perms = self.zconfig['admin']['attrs'].items()
    admin_list_name = self.zconfig['dlist']['admin_list_name']
    try:
      response = z_request.createAccount(account_name, attrs = account_perms)
    except ZimbraRequestError, e:
      if not e.response.get_fault_code() == 'account.ACCOUNT_EXISTS':
        abort(400, message = 'Unknown error: %s' % e.message)

      response = z_request.getAccount(account_name, attrs=['zimbraId'])
      account_zimbra_id = response['GetAccountResponse']['account']['id']
      # Close system account, this removes all distribution list from the account
      z_request.modifyAccount(account_zimbra_id, [('zimbraAccountStatus', 'closed')])
      z_request.modifyAccount(account_zimbra_id, [('zimbraAccountStatus', 'active')])

    response = z_request.getDistributionList('@'.join((admin_list_name, domain_name)))
    dlist_zimbra_id = response['GetDistributionListResponse']['dl']['id']
    z_request.addDistributionListMember(dlist_zimbra_id, [account_name])

  @property
  def _is_cacheable(self):
    return request.headers.get('Cache-Control') != 'no-cache'

  def get(self, service_name, domain_name):
    domain = abort_if_obj_doesnt_exist('name', domain_name, Domain)
    service = abort_if_obj_doesnt_exist('name', service_name, Service)

    if not service.service_type == 'ZIMBRA':
      abort(400, message = 'Only Zimbra type is allowed')

    cred = service.credentials
    if not cred:
      abort(400, message = 'Could not find any credentials for the service %s' % service.name)
    admin_user, admin_password = cred.identity, cred.secret
    default_system_domain = self.zconfig['admin']['default_domain_name']

    requested_login = '@'.join((self.request_user_login, domain_name))
    cache_redis_key = 'jenova:cache:preauth:%s' % requested_login
    cache_redis_key_domain = 'jenova:cache:preauthkey:%s' % default_system_domain
    cache_preauth_uri, cache_preauth_key = self.redis.mget(cache_redis_key, cache_redis_key_domain)
    if self._is_cacheable and cache_preauth_uri:
      try:
        # Check if cache uri is fine
        r = requests.head(cache_preauth_uri, verify = False, allow_redirects = False, timeout = 1.5)
        if r.status_code == 302:
          return '', 204, { 'Location' : cache_preauth_uri }
      except Exception, e:
        # Validation fail. Proceed without cache
        pass
      # Clear cache! Something went wrong! Next request will fix the problem
      self.redis.delete(cache_redis_key)

    preauth_key = hmac.new(str(uuid.uuid4), str(uuid.uuid4()), hashlib.sha256).hexdigest()
    if self._is_cacheable and cache_preauth_key:
      preauth_key = cache_preauth_key

    zr = ZimbraRequest(
      admin_url = service.service_api, 
      admin_user = admin_user, 
      admin_pass = admin_password
    )

    try:
      # Cache preauth key for default_system_domain.
      if cache_preauth_key != preauth_key:
        domainid = zr.getDomainId(default_system_domain)
        # Modify preauth_key. Same as: zmprov gdpak domain.tld
        response = zr.modifyDomain(domainid, [('zimbraPreAuthKey', preauth_key)])
        with self.redis.pipeline() as pipe:
          pipe.set(cache_redis_key_domain, preauth_key)
          pipe.expire(cache_redis_key_domain, 3600)
          pipe.execute()

      delegated_admin_account = '@'.join((self.request_user_login, default_system_domain))
      self._set_account_perms(zr, delegated_admin_account, domain_name)
    except ZimbraRequestError, e:
      # TODO: Log HERE other INFO
      abort(400, message = 'Unknown Error: %s' % e.message)

    preauth_uri = self._get_preauth_url(service.service_host, delegated_admin_account, preauth_key)
    cache_expire_time = self.zconfig['admin']['attrs']['zimbraAdminAuthTokenLifetime']
    
    # Cache preauth_uri based 
    with self.redis.pipeline() as pipe:
      pipe.set(cache_redis_key, preauth_uri)
      pipe.expire(cache_redis_key, cache_expire_time)
      pipe.execute()
    
    # Cannot handle 3xx in angularjs
    return '', 204, {'Location' : preauth_uri}

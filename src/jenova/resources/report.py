from flask_restful import abort
from redis import StrictRedis
import pickle
from jenova.resources.base import BaseResource, abort_if_obj_doesnt_exist
from jenova.components.zimbra import ZimbraReport
from jenova.components.tasks import update_zimbra_domain_report_task
from jenova.models import (
  Reseller, Client, DomainSchema, Domain, Service
)
from jenova.components.common import Config

class ResellerReportResource(BaseResource):
  def __init__(self):
    filters = ['id', 'name']
    super(ResellerReportResource, self).__init__(filters)
    config = Config.load()
    self.redis = StrictRedis(config['redishost'])

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

  def post(self, target_reseller):
    reseller = abort_if_obj_doesnt_exist(self.filter_by, target_reseller, Reseller)
    self.parser.add_argument('sync', type=str, choices=('0', '1'))
    reqdata = self.parser.parse_args()
    synchronous = reqdata.get('sync') and True
    job_id, status_code = None, 200

    domains = Domain.query\
      .filter(Reseller.id == Client.reseller_id)\
      .filter(Domain.client_id == Client.id)\
      .filter(Reseller.id == reseller.id)\
      .all()

    if synchronous:
      task_obj = update_zimbra_domain_report_task.apply
    else:
      task_obj = update_zimbra_domain_report_task.apply_async
    params = {}
    for domain in domains:
      for s in domain.services:
        if s.service_type == 'ZIMBRA':
          service = s
      if not service:
          self.logger.info('Could not find any zimbra service for domain %s' % domain.name)
          continue

      if not params.get(service.name):
        cred = service.credentials
        if not cred:
          abort(400, message = 'Could not find any credentials for the service %s' % service.name)

        admin_user, admin_password = cred.identity, cred.secret
        params[service.name] = {
          'zimbra_config' : {
            'service_api' : service.service_api,
            'admin_user' : admin_user,
            'admin_password' : admin_password
          },
          'domains' : [],
          'reseller_name' : target_reseller
        }
      params[service.name]['domains'].append(str(domain.name))

    for service in params:
      # Executes method apply (synchronous) or apply_async (asynchronous)
      result_task = task_obj(kwargs = params[service])
      if synchronous:
        if result_task.state == 'FAILURE':
          abort(400, message = 'Error completing task: %s' % result_task.result)
        status_code = 201
        job_id = result_task.id
      else:
        job_id = result_task.id
        status_code = 202

    return { 
      'response' : {
        'task_id' : job_id or '',
      } 
    }, status_code

  def get(self, target_reseller):
    self.parser.add_argument('export', type=bool, location='args') # TODO
    reqdata = self.parser.parse_args()

    export = reqdata.get('export') or False
    reseller = abort_if_obj_doesnt_exist(self.filter_by, target_reseller, Reseller)
    
    res = {
      'name' : reseller.name,
      'n_domains' : 0,
      'services' : {'zimbra' : {}}
    }
    
    reseller_key = 'jenova:reports:%s' % target_reseller
    for data in pickle.loads(self.redis.get(reseller_key)):
      try:
        # increment
        for edition in data['editions']:
          if not res['services']['zimbra'].get(edition):
            res['services']['zimbra'][edition] = 0
          res['services']['zimbra'][edition] += data['editions'][edition]
        
        res['n_domains'] += 1
      except Exception, e:
        self.logger.error("error getting reseller report: [%s]" % e)
      
    return {
      'response' : res
    }

class DomainReportResource(BaseResource):
  def __init__(self):
    filters = ['id', 'name']
    super(DomainReportResource, self).__init__(filters)

  # Overrided
  def is_forbidden(self, **kwargs):
    """ Check for access rules:
    A global admin must not have any restrictions.
    A requester must have access of your own domains (reseller) only if is an admin
    A requester must have access of your own domains (client)
    """
    if self.is_global_admin: return

  @property
  def scope(self):
    return 'zimbra'

  def get(self, target_domain, target_service):
    domain = abort_if_obj_doesnt_exist('name', target_domain, Domain)
    service = abort_if_obj_doesnt_exist('name', target_service, Service)

    cred = service.credentials
    if not cred:
      abort(400, message = 'Could not find any credentials for the service %s' % service.name)

    admin_user, admin_password = cred.identity, cred.secret
    report = ZimbraReport(
      admin_url = service.service_api,
      admin_user = admin_user,
      admin_pass = admin_password,
    )

    response = report.getEditionReport(domains=[target_domain])
    return {
      'response' : response
    }, 200
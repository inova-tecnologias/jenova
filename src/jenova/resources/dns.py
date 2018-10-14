from flask_restful import abort, Resource, reqparse
from datetime import datetime, timedelta
import hmac, hashlib, time, uuid, re, json, gzip, ast

from jenova.resources.base import BaseResource, abort_if_obj_doesnt_exist
from jenova.components import db, PowerDns, DnsError
from jenova.models import Domain, Service, DnsRecordsSchema, DnsSoaSchema, DnsRecordBackup, DnsRecordBackupSchema

class DnsBackupBaseResource(BaseResource):
  def __init__(self, filters, pdns):
    super(DnsBackupBaseResource, self).__init__(filters)
    self.pdns = pdns

  def backup_zone(self, service_host, dns_api_key, domain_name):
    self.pdns.config(pdns_server = service_host, api_key = dns_api_key)

    # create backup if haven't one in the last 24hrs.
    yesterday = datetime.now() - timedelta(days=1)
    if not DnsRecordBackup.query.filter(DnsRecordBackup.created_at >= yesterday).first():
      try:
        res = self.pdns.get_domain(domain_name)
      except DnsError, e:
        abort(e.status_code, message = e.message)
      compressed_records = res.json()['records']

      backup = DnsRecordBackup(
        domain = domain_name,
        records = compressed_records
      )

      self.logger.debug('backuping up zone: %s' % domain_name)

      db.session.add(backup)
      db.session.commit()

class DnsSOAResource(BaseResource):
  """ DNS SOA Resource representation.
  """
  def __init__(self):
    filters = ['id', 'name']
    super(DnsSOAResource, self).__init__(filters)
    self.pdns = PowerDns()

  @property
  def scope(self):
    return 'dns'

  # Overrided
  def is_forbidden(self, service_name, domain_name):
    """ Check for access rules:
    A global admin must not have any restrictions.
    A requester must have access of your own domains (reseller) only if is an admin
    A requester must have access of your own domains (client)
    """
    if self.is_global_admin: return
    if not domain_name:
      abort(412, message = 'Could not find domain_name!')

    # TODO: set the result into kwargs  
    domain = abort_if_obj_doesnt_exist('name', domain_name, Domain)

    # TODO: test this
    if self.request_user_reseller_id != domain.client.reseller_id:
      abort(403, message = 'Permission denied! Domain does not belong to the request user')

    if self.request_user_client_id != domain.client_id and not self.is_admin:
      abort(403, message = 'Permission denied! Domain does not belong to the request user')

  def post(self, service_name, domain_name):
    service = abort_if_obj_doesnt_exist('name', service_name, Service)
    domain = abort_if_obj_doesnt_exist('name', domain_name, Domain)

    if not service.credentials:
      abort(400, message = 'Could not find any credentials for the service %s' % service.name)
    cred = service.credentials
    self.pdns.config(pdns_server = service.service_host, api_key = cred.secret)
    try:
      domain_dns_data = self.pdns.create_domain(domain_name)
    except DnsError, e:
      abort(e.status_code, message = e.message)

    return DnsSoaSchema().dump(domain_dns_data).data, 201

  def get(self, service_name, domain_name):
    service = abort_if_obj_doesnt_exist('name', service_name, Service)
    domain = abort_if_obj_doesnt_exist('name', domain_name, Domain)

    if not service.credentials:
      abort(400, message = 'Could not find any credentials for the service %s' % service.name)
    cred = service.credentials
    
    self.pdns.config(pdns_server = service.service_host, api_key = cred.secret)
    try:
      domain_dns_data = self.pdns.get_domain(domain_name).json()
    except DnsError, e:
      abort(e.status_code, message = e.message)

    return DnsSoaSchema().dump(domain_dns_data).data

  def delete(self, service_name, domain_name):
    self.parser.add_argument('force', type=int, location='args', default=0)
    self.parser.add_argument('force-external', type=int, location='args', default=0)
    reqdata = self.parser.parse_args()
    delete_all_records = True if reqdata['force'] == 1 else False
    force_external = True if reqdata['force-external'] == 1 else False
    # DANGER!!! This will force deleting the external service without verifying if the domain exists.
    if force_external and not self.is_global_admin:
      abort(403, message = 'Permission denied! Not enough permissions to perform this operation!')

    if force_external:
      delete_all_records = True

    service = abort_if_obj_doesnt_exist('name', service_name, Service)
    if not force_external:
      domain = abort_if_obj_doesnt_exist('name', domain_name, Domain)
    
    if not service.credentials:
      abort(400, message = 'Could not find any credentials for the service %s' % service.name)
    cred = service.credentials
    
    self.pdns.config(pdns_server = service.service_host, api_key = cred.secret)
    try:
      self.pdns.delete_domain(domain_name, force = delete_all_records)
    except DnsError, e:
      abort(e.status_code, message = e.message)
    return '', 204


class DnsRecordsBackupResource(DnsBackupBaseResource):
  """ DNS Records Backup Endpoint.
  """
  def __init__(self):
    filters = ['id', 'name']
    super(DnsRecordsBackupResource, self).__init__(filters, PowerDns())

  @property
  def scope(self):
    return 'dns'

  # Overrided
  def is_forbidden(self, service_name, domain_name):
    """ Check for access rules:
    A global admin must not have restrictions.
    A requester must have access of your own domains (reseller) only if is an admin
    A requester must have access of your own domains (client)
    """
    if self.is_global_admin: return
    if not domain_name:
      abort(412, message = 'Could not find domain_name!')

    # TODO: set the result into kwargs  
    domain = abort_if_obj_doesnt_exist('name', domain_name, Domain)

    if self.request_user_reseller_id != domain.client.reseller_id:
      abort(403, message = 'Permission denied! Domain does not belong to the request reseller')

    if self.request_user_client_id != domain.client_id and not self.is_admin:
      abort(403, message = 'Permission denied! Domain does not belong to the request user')

  def get(self, service_name, domain_name):
    """ This method retrieves the backups available for restore.
    """
    service = abort_if_obj_doesnt_exist('name', service_name, Service)
    domain = abort_if_obj_doesnt_exist('name', domain_name, Domain)

    if not service.credentials:
      abort(400, message = 'Could not find any credentials for the service %s' % service.name)
    
    cred = service.credentials
    self.pdns.config(pdns_server = service.service_host, api_key = cred.secret)
    backups = DnsRecordBackup.query.filter_by(domain=domain_name)

    return {
      'backups' : DnsRecordBackupSchema(many=True).dump(backups).data
    }

  def put(self, service_name, domain_name):
    """ This method restores a backup by its id.
    """
    service = abort_if_obj_doesnt_exist('name', service_name, Service)
    domain = abort_if_obj_doesnt_exist('name', domain_name, Domain)

    if not service.credentials:
      abort(400, message = 'Could not find any credentials for the service %s' % service.name)
    
    cred = service.credentials
    self.pdns.config(pdns_server = service.service_host, api_key = cred.secret)
    
    self.parser.add_argument('backup_id', type=int, required=True)
    reqdata = self.parser.parse_args()

    backup = DnsRecordBackup.query.filter_by(id=reqdata['backup_id']).first()
    
    self.pdns.restore_zone(
          domain_name=domain_name,
          records=ast.literal_eval(backup.records)
    )
    return { 'response' : 'Zone Successfuly Restored' }

  def post(self, service_name, domain_name):
    """ Method for backing up zone.
    """
    service = abort_if_obj_doesnt_exist('name', service_name, Service)
    domain = abort_if_obj_doesnt_exist('name', domain_name, Domain)

    if not service.credentials:
      abort(400, message = 'Could not find any credentials for the service %s' % service.name)
    
    self.backup_zone(
      service_host = service.service_host, 
      dns_api_key = service.credentials.secret, 
      domain_name = domain_name
    )

    backup = DnsRecordBackup.query.filter_by(domain=domain_name).first()
    return { 
      'response' : {
        'domain' : backup.domain,
        'records' : backup.records
      }
    }, 201

class DnsRecordsResource(DnsBackupBaseResource):
  """ DNS Records Resource representation.
  """
  def __init__(self):
    filters = ['id', 'name']
    super(DnsRecordsResource, self).__init__(filters, PowerDns())

  @property
  def scope(self):
    return 'dns'

  # Overrided
  def is_forbidden(self, **kwargs):
    """ Check for access rules:
    A global admin must not have restrictions.
    A requester must have access of your own domains (reseller) only if is an admin
    A requester must have access of your own domains (client)    
    """
    if self.is_global_admin: return

    domain_name = kwargs.get('domain_name') or abort(400, message = 'Could not find "domain_name"')
    # TODO: set the result into kwargs  
    domain = abort_if_obj_doesnt_exist('name', domain_name, Domain)

    if self.request_user_reseller_id != domain.client.reseller_id:
      abort(403, message = 'Permission denied! Domain does not belong to the request reseller')

    if self.request_user_client_id != domain.client_id and not self.is_admin:
      abort(403, message = 'Permission denied! Domain does not belong to the request user')
    
  def put(self, service_name, domain_name, dns_type, name):
    ''' *UPDATE* Idempotent. It will replace the contents of the request records only! '''
    service = abort_if_obj_doesnt_exist('name', service_name, Service)
    domain = abort_if_obj_doesnt_exist('name', domain_name, Domain)

    self.logger.info('update dns record')
    if not service.credentials:
      abort(400, message = 'Could not find any credentials for the service %s' % service.name)
    cred = service.credentials
    
    service_name = service_name.lower()
    domain_name = domain_name.lower()
    dns_type = dns_type.upper()
    name = name.lower()

    self.pdns.config(pdns_server = service.service_host, api_key = cred.secret)
    
    self.parser.add_argument('new_content', type=str, required=True)
    self.parser.add_argument('new_ttl', type=int)
    self.parser.add_argument('new_registry_name', type=str, required=True)
    self.parser.add_argument('old_content', type=str, required=True)
    self.parser.add_argument('old_ttl', type=int, required=True)

    reqdata = self.parser.parse_args()

    # adjust name when domain not passed
    if not re.findall(r'\.', name):
      name = name + '.%s' % domain_name

    # Backup Zone before updating it
    self.backup_zone(
      service_host = service.service_host, 
      dns_api_key = cred.secret, 
      domain_name = domain.name
    )
    try:
      response = self.pdns.update_record(
        domain_name = domain.name,
        dns_type = dns_type,
        old_registry_name = name,
        new_content = reqdata['new_content'],
        new_ttl = reqdata.get('new_ttl') or 3600, # Default: 1 hour
        old_content = reqdata['old_content'],
        old_ttl = reqdata['old_ttl'],
        new_registry_name = reqdata['new_registry_name']
      )
    except DnsError, e:
      abort(e.status_code, message = e.message)
    except Exception, e:
      # TODO: Log Exception
      abort(500, message = 'Error updating record: %s' % e)

    return DnsRecordsSchema().dump(response.json()).data, 200

  def post(self, service_name, domain_name, dns_type, name):
    ''' *CREATE* All existing records RRs matching 'dns_type' and 'name' will be deleted, 
    and will be updated with the new records. Same behavior as REPLACE in PowerDns API spec
    '''
    service = abort_if_obj_doesnt_exist('name', service_name, Service)
    domain = abort_if_obj_doesnt_exist('name', domain_name, Domain)

    if not service.credentials:
      abort(400, message = 'Could not find any credentials for the service %s' % service.name)
    cred = service.credentials

    service_name = service_name.lower()
    domain_name = domain_name.lower()
    dns_type = dns_type.upper()
    name = name.lower()

    self.pdns.config(pdns_server = service.service_host, api_key = cred.secret)
    
    self.parser.add_argument('content', type=str, required=True)
    self.parser.add_argument('ttl', type=int)
    reqdata = self.parser.parse_args()

    # adjust name when domain not passed
    if not re.findall(r'\.', name):
      name = name + '.%s' % domain_name

    try:
      response = self.pdns.create_record(
        domain_name = domain_name, 
        dns_type = dns_type,
        registry_name = name,
        content = reqdata['content'],
        replace = False,
        ttl = reqdata.get('ttl') or 3600 # Default: 1 hour
      )
    
      return DnsRecordsSchema().dump(response.json()).data, 201
    except DnsError, e:
      abort(e.status_code, message = e.message)
    except Exception, e:
      # TODO: Log exception!
      abort(500, message = 'Error creating record: %s' % e)

  def delete(self, service_name, domain_name, dns_type, name, content, ttl):
    service = abort_if_obj_doesnt_exist('name', service_name, Service)
    domain = abort_if_obj_doesnt_exist('name', domain_name, Domain)

    if not service.credentials:
      abort(400, message = 'Could not find any credentials for the service %s' % service.name)
    cred = service.credentials
    self.pdns.config(pdns_server = service.service_host, api_key = cred.secret)

    # Backup Zone before deleting it
    self.backup_zone(
      service_host = service.service_host, 
      dns_api_key = cred.secret, 
      domain_name = domain.name
    )

    try:
      response = self.pdns.delete_record(
        domain_name = domain_name,
        dns_type = dns_type,
        registry_name = name,
        content = content,
        ttl = int(ttl)
      )

      return response.json()
    except DnsError, e:
      abort(e.status_code, message = e.message)
    except Exception, e:
      # TODO: Log this exeception!
      abort(500, message = 'Error deleting record: %s' % e)
      
    return '', 204
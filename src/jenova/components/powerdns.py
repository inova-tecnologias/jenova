import json, requests, re, ast
from flask_restful import abort
from .exceptions import DnsError
from datetime import datetime, timedelta
from .common import logger
from time import sleep

# TODO: Move this to config file 
DNSTYPES = ['A', 'AAAA', 'MX', 'CNAME', 'SRV', 'TXT', 'PTR', 'NS']
NAMESERVERS = ['mydns5.inova.com.br', 'mydns6.inova.com.br']
SOA_CONTENT = 'dns1.inova.com.br hostmaster.inova.com.br %s01 10800 7200 604800 86400'
SOA_TTL = 3600
DEFAULT_TTL = 86400

# TODO: Change HTTP to HTTPS 
# TODO: Restore capabiliies.
# TODO: Delete RR/multiple name/type entries.
class PowerDns(object):
  def __init__(self, pdns_server='localhost', api_key='changeme'):
    ''' Make requests to PowerDNS API. Starting with version 3.4
    :param pdns_server: Hostname of the powerdns server
    :param api_key: X-API-Key of the powerdns. Default: changeme
    Ref: https://doc.powerdns.com/md/httpapi/api_spec/
    '''
    self.url = 'http://%s/servers/localhost/zones' % pdns_server
    self.headers = { 'X-API-Key' : api_key }

  def config(self, pdns_server, api_key='changeme'):
    self.url = 'http://%s/servers/localhost/zones' % pdns_server
    self.headers = { 'X-API-Key' : api_key }

  def get_response(self, response):
    status_code = 400

    if response.status_code not in [200, 201, 204]:
      msg = 'Error processing request. %s. code: %s' % (response.text, response.status_code)
      msg_error = response.json().get('error')
      if msg_error and re.match('Could not find domain', msg_error):
        status_code = 404
        msg = 'Domain Not Found'
      elif msg_error and re.match("Domain '.+' already exists", msg_error):
        status_code = 409
        msg = 'Domain already exists'

      raise DnsError(
        message = msg,
        response = response,
        status_code = status_code
      )
    return response

  def validate_record(self, dns_type, domain_name='', registry_name='',  content=''):
    '''This method helps to avoid common configuration errors. Work always in progress 
    depending of users criativity to find bugs and break stuff. 

    TODO: RFC compliance as many as possible: 
      - https://www.isc.org/community/rfcs/dns/
      - https://tools.ietf.org/html/rfc1537

    :param str:dns_type : DNS Type of a record you're trying to validate.
    :param str:domain_name : Domain Name related.
    :param str:registry_name : Record name.
    :param str:content : Record content.
    
    Returns: raise value error if occours.
    '''

    # if invalid dns type 
    if dns_type.upper() not in DNSTYPES:
      raise ValueError('Could not process request. Unsupported dns type: %s' % ', '.join(DNSTYPES))

    # if registry name is out of zone.
    if registry_name and domain_name:
      if not re.findall(r'%s$' % domain_name, registry_name):
        raise ValueError('Could not process request. Name is out of zone: %s domain: %s' % (registry_name, domain_name))

    '''
    try:
      if content and dns_type == 'SRV':
        prio, weight, port, target = content.split()
      elif content and dns_type == 'MX':
        prio, target = content.split()
    except Exception, e:
      raise DnsError('Invalid format for dns type %s. %s' % (dns_type, content))
    '''


  def _join_records(self, oldrecords, newrecords):
    result = []
    for oldrec in oldrecords:
      if oldrec['disabled'] == True:
        raise ValueError('%s - Must not be disabled. Could cause unwanted effect' % oldrec['content'])
      for newrec in newrecords:
        if newrec['name'] == oldrec['name'] and newrec['type'] == oldrec['type']:
          result.append(oldrec)
          break
    return result + newrecords

  def create_domain(self, domain_name, comment=''):
    ''' Create a new SOA in powerdns. Returns a requests.Response object
    '''
    serial = datetime.now().strftime('%Y%m%d')
    new_domain_data = {
      'name' : domain_name,
      'kind' : 'native',
      'masters' : [],
      'nameservers' : NAMESERVERS,
      'records' : [
        {
          'content' : SOA_CONTENT % serial,
          'name' : domain_name,
          'ttl' : SOA_TTL,
          'type' : 'SOA',
          'disabled' : False
        }
      ]
    }
    response = requests.post(self.url, data=json.dumps(new_domain_data), headers=self.headers)
    return self.get_response(response)

  def delete_domain(self, domain_name, force=False):
    ''' Delete an entire domain and all records.
    Returns a requests.Response object
    '''
    r = self.get_domain(domain_name).json()

    if not force:
      if len(r['records']) > 1:
        raise DnsError(
          message = 'Delete all records before deleting domain: %s records found' % len(r['records']),
          response = None,
          status_code = 409
        )

    url = '%s/%s' % (self.url, domain_name)
    response = requests.delete(url, headers=self.headers)
    return self.get_response(response)   

  def get_domain(self, domain_name):
    ''' Returns a requests.Response object
    '''
    url = '%s/%s' % (self.url, domain_name)
    return self.get_response(requests.get(url, headers = self.headers))
  
  def delete_record(self, domain_name, dns_type, 
                    registry_name, content, ttl=3600):
    '''Description
    ==============
      Delete records. This method validates if a record exists and delete it.

    Params
    ======
      :param str:domain_name: The target domain
      :param str:dns_type: The type of the DNS: 'A', 'CNAME', 'MX', etc.
      :param str:registry_name: The name of the registry
      :param str:content: The content of the registry
      :param int:ttl: Time to Live of the record. Default to 3600

    Response
    ========
    Returns a requests.Response object
    '''

    url = '%s/%s' % (self.url, domain_name)
    dns_type = dns_type.upper()
    
    self.validate_record(domain_name=domain_name,
                        dns_type=dns_type,
                        registry_name=registry_name)

    record = {
      'content' : content,
      'name' :  registry_name,
      'ttl' : ttl,
      'type' : dns_type,
      'disabled' : False
    }

    # get all records
    res = self.get_domain(domain_name)
    backup_records = res.json()['records']
    current_records = res.json()['records']

    # Be carefull, if 'disabled' is True, this will fail!
    # exception if record not found.
    if record not in current_records:
      raise DnsError(
        message = 'Record %s and its content %s not found' % (record['name'], record['content']),
        response = None,
        status_code = 404
      )

    # Replace records will prevent excluding older records (even if it's duplicated)
    rrsets =  self._build_rrsets( 
      oldrecord = record,
      records = current_records,
      delete = True
    )

    try:
      # update with changes made.
      response = self.get_response(
        requests.patch(url, data=json.dumps(rrsets), headers=self.headers)
      )
      return response
    except Exception, e:
      # do full rollback if an error happens. It sucks but pds api does weird things when something 
      # goes wrong.
      try:
        logger.error('Error updating records. trying to roll back: %s' % e )
        response = requests.patch(url, data=json.dumps(rrsets), headers=self.headers)
        return e
      except Exception, e:
        # i really hope you have a backup if reach this point.
        # working on application backup for the next version.
        errmsg = 'Error rolling back'
        logger.error('%s: %s' % (errmsg, e))
        raise Exception('%s: %s' % (errmsg, e))
  
  def restore_zone(self, domain_name, records):
    """ This method restores a backup from a list of records. The whole domain will be deleted
    and then the entries will be replaced. If an error occours during the restore all data may be lost.

    :param str:domain_name Domain name to restore the records.
    :param list:records List of records to restore.
    """
    rrsets = []  
    for record in records:
      rr = False
      for rset in rrsets:
        if rset['name'] == record['name'] and rset['type'] == record['type']:
          record['disabled'] = False
          rset['records'].append(record)
          rr = True

      if not rr:
        rrsets.append({
          'name' : record['name'],
          'type' : record['type'],
          'changetype' : 'REPLACE',
          'records' : [
           {
             'name' : record['name'],
             'type' : record['type'],
             'content' : record['content'],
             'ttl' : record['ttl'],
             'disabled' : False
           }]
        },)


    try:
      url = '%s/%s' % (self.url, domain_name)
      self.delete_domain(domain_name = domain_name, force=True)
      self.create_domain(domain_name = domain_name)

      response = requests.patch(url, data=json.dumps({'rrsets': rrsets}), headers=self.headers)
      logger.debug('restoring zone: %s' % domain_name)
      return self.get_response(response)
    except Exception, e:
      logger.critical('Error restoring zone: %s' % e)


  def _build_rrsets(self, records, oldrecord, newrecord='', replace=False, delete=False):
    '''Description
      ============
      This method receives a records list and returns a json object used in patch updates. 
      It is basically used to update or delete records(RR or multiple values/types is supported)
      so you can get the original records modify as you want, send it to me and i'll format for you to send to pdns api.

      Params
      ==========
        :param list:records - A list of records originaly by:
                              res = self.get_domain(domain_name)
                              records = res.json()['records']

      Ref
      ==== 
        - https://doc.powerdns.com/md/httpapi/api_spec/#url-apiv1serversserver95idzoneszone95id
    '''
    rrsets = []

    # create sumary to identify Multi value entries such as RR.
    srecord = {} 
    for record in records:
      if record['name'] not in srecord:
        srecord[record['name']] = {}

      if record['type'] not in srecord[record['name']]:
        srecord[record['name']][record['type']] = []
       
      srecord[record['name']][record['type']].append({
        'content' : record['content'],
        'ttl' : record['ttl']
        })
    
    # parse current records looking for necessary changes
    # # Updates/deletes only the requested registry.
    for record in records:
      
      if (record['name'] == oldrecord['name'] and
          record['type'] == oldrecord['type'] and
          record['content'] == oldrecord['content'] and
          record['ttl'] == oldrecord['ttl']):

        rrsets_records = []
        # summary for name/type registries.
        record_sumary = srecord[record['name']][record['type']]

        #  ***RR/MULTI-VALUE RECORDS***
        # If summary has more than one value means it's a multi-value registry(RR). Here i
        # create a rrsets_records with the original RR registries **BUT** the one you're
        # trying to update (oldrecord) or delete. In case of:
        # 
        # UPDATE (update = True):
        #   Not always an update will be with the same name/type, if that's the case, we need two rrsets,
        #   one creating a new record and other updating the RRs without the old record.
        #   If the update is with the same name and type we will append the changes and only one rrset will be necessary.
        # DELETE (delete = True)
        #   Dont need to end the method. We will removed the matched record and we can overwrite the records without it.
         
        if len(record_sumary) > 1:
          for rr_record in record_sumary:
            if rr_record['content'] == record['content']: # don't append the current record. if is a update will do this later, if is a delete. Its done, we already removed from the original values(name/type records)
              continue

            rrsets_records.append({
              'name' : record['name'],
              'type' : record['type'],
              'content' : rr_record['content'],
              'ttl' : rr_record['ttl'],
              'disabled' : False
            })

          if delete:
            rrsets.append({
              'name' : record['name'],
              'type' : record['type'],
              'changetype' : 'REPLACE',
              'records' : rrsets_records
            },)

            logger.debug('__SMART_REBUILD_REMOVE_RR')
            logger.debug(json.dumps({'rrsets': rrsets}, indent=2))
            return {'rrsets': rrsets}
          # DELETE RR ENTRIES END HERE

        # Update record
        if replace:
          # when a record changes its name or type we must delete the registry 
          # and create a brand new one.
          if (record['name'] != newrecord['name'] or
              record['type'] != newrecord['type']):
            delete = True
            # Joining records will prevent excluding older records (even if it's duplicated)
            rrsets_records = self._join_records(
              oldrecords = records,
              newrecords = [newrecord]
            )
          else:
            rrsets_records.append({
                'name' : newrecord['name'],
                'type' : newrecord['type'],
                'content' : newrecord['content'],
                'ttl' : newrecord['ttl'],
                'disabled' : False
            })

          rrsets.append({
            'name' : newrecord['name'],
            'type' : newrecord['type'],
            'changetype' : 'REPLACE',
            'records' : rrsets_records
          },)

        # Deletes single registry(only regular registries **NOT** RR).
        if delete:
          rrsets.append({
            'name' : oldrecord['name'],
            'type' : oldrecord['type'],
            'changetype' : 'DELETE',
            'records' : [
            {
              'name' : oldrecord['name'],
              'type' : oldrecord['type'],
            }
          ]
          },)

    logger.debug('__SMART_REBUILD')
    logger.debug(json.dumps({'rrsets': rrsets}, indent=2))
    return {'rrsets': rrsets}

  def update_record(self, domain_name, dns_type, 
                    new_registry_name, new_content, old_content, 
                    old_ttl, old_registry_name, new_ttl=3600):
    '''Description
    ==============
      Update records. This method validates if a record exists and update it.
    Unfortunately PDNS API doest have a id/record :(

    Params
    ======
      :param str:domain_name: The target domain
      :param str:dns_type: The type of the DNS: 'A', 'CNAME', 'MX', etc.
      :param str:new_registry_name: The name of the registry
      :param str:new_content: The content of the registry
      :param int:new_ttl: Time to Live of the record. Default to 3600
      :param str:old_registry_name: The name of the original registry
      :param str:old_content: The content of the original registry
      :param int:old_ttl: Time to Live of the original record.

    TODO
    ======
      - support update type of an record.
      - support disabled/enabled record.
    '''

    url = '%s/%s' % (self.url, domain_name)
    self.validate_record(domain_name=domain_name,
                        dns_type=dns_type,
                        registry_name=new_registry_name)

    old_record = {
      'content' : old_content,
      'name' : old_registry_name,
      'ttl' : old_ttl,
      'type' : dns_type,
      'disabled' : False
    }

    new_record = {
      'content' : new_content,
      'name' : new_registry_name,
      'ttl' : new_ttl,
      'type' : dns_type,
      'disabled' : False
    }


    # get all records
    res = self.get_domain(domain_name)
    backup_records = res.json()['records']
    current_records = res.json()['records']

    # Be carefull, if 'disabled' is True, this will fail!
    # exception if record not found.
    if old_record not in current_records:
      raise ValueError('Record %s and its content %s not found' % \
        (old_record['name'], old_record['content']))

    # exception if new record already exist
    if new_record in current_records:
      raise ValueError('Record %s and its content %s already exists' % \
        (new_record['name'], new_record['content']))
    
    # Replace records will prevent excluding older records (even if it's duplicated)
    rrsets =  self._build_rrsets(
      replace = True,
      oldrecord = old_record,
      newrecord = new_record,
      records = current_records
    )

    try:
      # update with changes made.
      response = requests.patch(url, data=json.dumps(rrsets), headers=self.headers)
      return self.get_response(response)
    except Exception, e:
      # do full rollback if an error happens. It sucks but pds api does weird things when something 
      # goes wrong.
      try:
        logger.error('Error updating records. trying to roll back: %s' % e )
        self.restore_zone(domain_name = domain_name, records = backup_records)
      except Exception, e:
        errmsg = 'Error rolling back'
        logger.critical('%s: %s' % (errmsg, e))
        raise Exception('%s: %s' % (errmsg, e))

  def create_record(self, domain_name, dns_type, registry_name, content, replace=False, ttl=3600):
    # STUFF TO DO.
    ''' Create or Update records. This method tries to handle duplication of registries and deletion.
    REPLACE 'changetype' will exclude every registry matching 'name' and 'type'. This method tries to 
    prevent this behavior. 
    Ref: https://doc.powerdns.com/md/httpapi/api_spec/#url-serversserver95idzoneszone95id (check changetype)
    :param domain_name: The target domain
    :param dns_type: The type of the DNS: 'A', 'CNAME', 'MX', etc.
    :param registry_name: The name of the registry
    :param content: The content of the registry
    :param replace: Replace records matching 'name' and 'type', otherwise maintain old ones. Default: False
    :param ttl: Time to Live of the record. Default to 3600
    Returns a requests.Response object
    '''
    url = '%s/%s' % (self.url, domain_name)
    self.validate_record(domain_name=domain_name,
                  dns_type=dns_type,
                  registry_name=registry_name)

    rrsets = {
      'rrsets' : [
        {
          'name' : registry_name,
          'type' : dns_type,
          'changetype' : 'REPLACE',
          'records' : [{
              'content' : content,
              'name' : registry_name,
              'ttl' : ttl,
              'type' : dns_type,
              'disabled' : False
            }]
        },]
    }

    if not replace:
      # Need to check for duplicate (same 'name', 'type' and 'content') registries in the zone.
      response = self.get_response(requests.get(url, headers = self.headers))
      old_records = response.json()['records']
      request_record = rrsets['rrsets'][0]['records'][0]

      # Be carefull, if 'disabled' is True, this will fail!
      if request_record in old_records:
        raise ValueError('The record already exists %s for the name %s' % \
          (request_record['content'], request_record['name']))

      # Joining records will prevent excluding older records (even if it's duplicated)
      rrsets['rrsets'][0]['records'] = self._join_records(
        oldrecords = old_records,
        newrecords = [request_record]
      )

    response = requests.patch(url, data=json.dumps(rrsets), headers=self.headers)
    return self.get_response(response)

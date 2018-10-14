from flask_restful import abort
from datetime import datetime
from time import sleep

from jenova.resources.base import BaseResource, abort_if_obj_doesnt_exist
from jenova.models import Service, ServiceSchema, ServiceCredentials
from jenova.components import db

class ServiceResource(BaseResource):
  def __init__(self):
    filters = ['id', 'name']
    super(ServiceResource, self).__init__(filters)

  def get(self, target_service):
    if (target_service == 'all'):
      service = Service.query.all()
    else:
      service = abort_if_obj_doesnt_exist(self.filter_by, target_service, Service)
    
    hasmany = False
    if type(service) == list:
      hasmany = True

    return { 'response': ServiceSchema(many=hasmany).dump(service).data }

  def delete(self, target_service):
    service = abort_if_obj_doesnt_exist(self.filter_by, target_service, Service)
    db.session.delete(service)
    db.session.commit()
    return '', 204

  def put(self, target_service):
    service = abort_if_obj_doesnt_exist(self.filter_by, target_service, Service)

    self.parser.add_argument('service_host', type=str)
    self.parser.add_argument('service_type', type=str, choices=('ZIMBRA', 'MXHERO', 'DNS'))
    self.parser.add_argument('service_desc', type=str)
    self.parser.add_argument('credentials_identity', type=str)
    self.parser.add_argument('credentials_secret', type=str)
    self.parser.add_argument('service_url', type=str)
    self.parser.add_argument('service_api', type=str)

    reqdata = self.parser.parse_args()

    service.service_host = reqdata.get('service_host') or service.service_host
    service.service_desc = reqdata.get('service_desc') or service.service_desc
    service.service_url = reqdata.get('service_url') or service.service_url
    service.service_api = reqdata.get('service_api') or service.service_api
    service.service_type = reqdata.get('service_type') or service.service_type

    if reqdata.get('credentials_identity'):
      if not 'credentials_secret' in reqdata:
        abort(400, message = 'Missing credentials_secret parameter')

      service.credentials = ServiceCredentials(
        service_id = service.id,
        identity = reqdata['credentials_identity'] or service.credentials.identity,
        secret = reqdata['credentials_secret'] or service.credentials.secret
      )
    elif reqdata.get('credentials_secret'):
      service.credentials = ServiceCredentials(
        service_id = service.id,
        secret = reqdata['credentials_secret'] or service.credentials.secret
      )

    db.session.commit()

    return '', 204

  def post(self, target_service):
    target_service = target_service.lower()
    if Service.query.filter_by(name=target_service).first():
      abort(409, message='The service {} already exists'.format(target_service))

    self.parser.add_argument('service_host', type=str, required=True)
    self.parser.add_argument('service_type', type=str, required=True, choices=('ZIMBRA', 'MXHERO', 'DNS'))
    self.parser.add_argument('service_desc', type=str, required=True)
    self.parser.add_argument('credentials_identity', type=str)
    self.parser.add_argument('credentials_secret', type=str)
    self.parser.add_argument('service_url', type=str)
    self.parser.add_argument('service_api', type=str)

    reqdata = self.parser.parse_args()

    service = Service(name = target_service,
      service_host = reqdata['service_host'],
      service_desc = reqdata['service_desc'],
      service_url = reqdata.get('service_url'),
      service_api = reqdata.get('service_api'),
      service_type = reqdata.get('service_type'),
    )
    db.session.add(service)
    db.session.flush()
    

    if reqdata.get('credentials_identity'):
      if not 'credentials_secret' in reqdata:
        abort(400, message = 'Missing credentials_secret parameter')
      
      service.credentials = ServiceCredentials(
        service_id = service.id,
        identity = reqdata['credentials_identity'],
        secret = reqdata['credentials_secret']
      )
    elif reqdata.get('credentials_secret'):
      service.credentials = ServiceCredentials(
        service_id = service.id,
        secret = reqdata['credentials_secret']
      )

    db.session.add(service)
    db.session.commit()

    service = Service.query.filter_by(name=target_service).one()
    return { 
      'response' : {
        'service_id' : service.id
      }
    }, 201
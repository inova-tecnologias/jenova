from flask_restful import abort, request
from datetime import datetime
import uuid

from jenova.resources.base import BaseResource, abort_if_obj_doesnt_exist
from jenova.models import (
  Client, Reseller, Domain, User, 
  ClientSchema, ResellerSchema,  DomainSchema, Service, ResellerServices
)
from jenova.components import Security
from jenova.components import db

class ResellerListResource(BaseResource):
  def __init__(self):
    filters = ['name']
    super(ResellerListResource, self).__init__(filters)

  def get(self):
    self.parser.add_argument('limit', type=int, location='args')
    self.parser.add_argument('offset', type=int, location='args')
    reqdata = self.parser.parse_args()
    offset, limit = reqdata.get('offset') or 0, reqdata.get('limit') or 25
    
    resellers = Reseller.query\
      .offset(offset)\
      .limit(limit)\
      .all()
      
    if not resellers:
      abort(404, message = 'Could not find any reseller')
    return {
      'response' : {
        'resellers' : ResellerSchema(many=True).dump(resellers).data
      }
    }

class ResellerListByQueryResource(BaseResource):
  def __init__(self):
    filters = ['name']
    super(ResellerListByQueryResource, self).__init__(filters)

  def get(self, by_name_query):
    self.parser.add_argument('limit', type=int, location='args')
    self.parser.add_argument('offset', type=int, location='args')
    reqdata = self.parser.parse_args()
    offset, limit = reqdata.get('offset') or 0, reqdata.get('limit') or 100
    if offset > limit or limit > 100:
      abort(400, message = 'Wrong offset/limit specified. Max limit permited: 100')

    total_records = Reseller.query\
      .filter(Reseller.name.like('%' + by_name_query + '%'))\
      .count()

    if total_records == 0:
      abort(404, message = 'Could not find any reseller using query: %s' % by_name_query)

    resellers = Reseller.query\
      .filter(Reseller.name.like('%' + by_name_query + '%'))\
      .offset(offset)\
      .limit(limit)\
      .all()

    response_headers = {}
    if limit < total_records:
      new_offset = limit + 1
      new_limit = new_offset + (limit - offset)
      response_headers['Location'] = '%s?offset=%s&limit=%s' % (request.base_url, new_offset, new_limit)

    return {
      'response' : {
        'resellers' : ResellerSchema(many=True).dump(resellers).data
      }
    }, 200, response_headers


class ResellerServicesListResource(BaseResource):
  def __init__(self):
    filters = ['id', 'name']
    super(ResellerServicesListResource, self).__init__(filters)

class ResellerDomainListResource(BaseResource):
  def __init__(self):
    filters = ['id', 'name']
    super(ResellerDomainListResource, self).__init__(filters)

  # def get(self, target_reseller):
  #   reseller = abort_if_obj_doesnt_exist(self.filter_by, target_reseller, Reseller)

  def get(self, target_reseller):
    self.parser.add_argument('limit', type=int, location='args')
    self.parser.add_argument('offset', type=int, location='args')
    reqdata = self.parser.parse_args()
    offset, limit = reqdata.get('offset') or 0, reqdata.get('limit') or 25

    reseller = abort_if_obj_doesnt_exist(self.filter_by, target_reseller, Reseller)

    count = Domain.query\
      .filter(Reseller.id == Client.reseller_id)\
      .filter(Domain.client_id == Client.id)\
      .filter(Reseller.id == reseller.id)\
      .count()

    domains = Domain.query\
      .filter(Reseller.id == Client.reseller_id)\
      .filter(Domain.client_id == Client.id)\
      .filter(Reseller.id == reseller.id)\
      .offset(offset)\
      .limit(limit)\
      .all()

    if not domains:
      abort(404, message='Could not find any domains')

    return {
      'response' : {
        'domains' : DomainSchema(many=True).dump(domains).data,
        'total' : count
      }
    }    

class ResellerResource(BaseResource):
  def __init__(self):
    filters = ['id', 'name']
    super(ResellerResource, self).__init__(filters)


  def get(self, target_reseller):
    reseller = abort_if_obj_doesnt_exist(self.filter_by, target_reseller, Reseller)
    return { 
      'response' : {
        'resellers' : ResellerSchema().dump(reseller).data
      }
    }

  def delete(self, target_reseller):
    reseller = abort_if_obj_doesnt_exist(self.filter_by, target_reseller, Reseller)
    if reseller.clients.all():
      abort(409, message = 'The reseller still have clients')
    db.session.delete(reseller)
    db.session.commit()
    return '', 204

  def put(self, target_reseller):
    reseller = abort_if_obj_doesnt_exist(self.filter_by, target_reseller, Reseller)
    self.parser.add_argument('email', type=str)
    self.parser.add_argument('company', type=unicode)
    self.parser.add_argument('phone', type=str)
    self.parser.add_argument('enabled', type=bool)
    self.parser.add_argument('services', type=str, action='append')

    reqdata = self.parser.parse_args(strict=True)

    reseller.email = reqdata.get('email') or reseller.email
    reseller.company = reqdata.get('company') or reseller.company
    reseller.phone = reqdata.get('phone') or reseller.phone
    if reqdata.get('enabled') != None:
      reseller.enabled = reqdata.get('enabled')
    
    # Delete all services from the association proxy
    del reseller.services[:]
    for svc in reqdata.get('services') or []:
      service = abort_if_obj_doesnt_exist('name', svc, Service)
      reseller.services.append(service)

    db.session.commit()
    return '', 204

  def post(self, target_reseller):
    target_reseller = target_reseller.lower()
    if Reseller.query.filter_by(name=target_reseller).first():
      abort(409, message='The reseller {} already exists'.format(target_reseller))

    # TODO: Validate email field
    self.parser.add_argument('email', type=str, required=True)
    self.parser.add_argument('company', type=unicode, required=True)
    self.parser.add_argument('phone', type=str)
    self.parser.add_argument('login_name', type=unicode, required=True)
    self.parser.add_argument('login', type=str, required=True)
    self.parser.add_argument('password', type=str, required=True)
    self.parser.add_argument('services', type=str, action='append')

    reqdata = self.parser.parse_args(strict=True)

    reseller = Reseller(name = target_reseller,
      email = reqdata['email'],
      company = reqdata['company'],
    )
    reseller.phone = reqdata.get('phone')

    # associate services to reseller
    if reqdata.get('services'):
      for service_name in set(reqdata['services']):
        service = Service.query.filter_by(name = service_name).first()
        if not service:
          db.session.rollback()
          abort(404, message = 'Could not find service: %s' % service)

        reseller_service = ResellerServices(
          reseller = reseller,
          service = service
        )
        db.session.add(reseller_service)
        db.session.flush()

    user = User(login = reqdata['login'],
      name = reqdata['login_name'],
      email = reqdata['email'],
      password = Security.hash_password(reqdata['password']),
      admin = True
    )
    
    reseller.user = user
    db.session.add(reseller)
    db.session.commit()

    reseller = Reseller.query.filter_by(name=target_reseller).first()

    return {
      'response' : { 
        'reseller_id' : reseller.id, 
        'user_id' : user.id
      } 
    }, 201

class ClientListResource(BaseResource):
  def __init__(self):
    filters = ['id', 'name']
    super(ClientListResource, self).__init__(filters)

  @property
  def scope(self):
    return 'client'

  # Overrided
  def is_forbidden(self, **kwargs):
    """ Check for access rules:
    A global admin must not have any restrictions.
    Only an admin must access this resource.
    A requester must have access of your own clients
    """
    target_reseller = kwargs.get('target_reseller')
    if self.is_global_admin: return
    if not self.is_admin and not request.method == 'GET':
      abort(403, message = 'Permission denied! Does not have enough permissions for access this resource')

    if not target_reseller:
      abort(400, message = 'Could not find "target_reseller"')

    reseller = abort_if_obj_doesnt_exist('name', target_reseller, Reseller)
    if self.request_user_reseller_id != reseller.id:
      abort(403, message = 'Permission denied! The reseller does not belongs to the requester.')

  def get(self, **kwargs):
    target_reseller = kwargs.get('target_reseller')
    self.parser.add_argument('limit', type=int, location='args')
    self.parser.add_argument('offset', type=int, location='args')
    by_name_query = kwargs.get('by_name_query') or ''
    reqdata = self.parser.parse_args()
    offset, limit = reqdata.get('offset') or 0, reqdata.get('limit') or 25

    if self.is_global_admin:
      clients = Client.query \
      .filter(Client.name.like('%' + by_name_query + '%') | Client.company.like('%' + by_name_query + '%'))\
      .offset(offset)\
      .limit(limit)\
      .all()
      return {
        'response' : { 
          'reseller_id' : None,
          'clients' : ClientSchema(many=True).dump(clients).data
        } 
      }
    elif self.is_admin:
      reseller = abort_if_obj_doesnt_exist(self.filter_by, target_reseller, Reseller)
      if by_name_query:
        clients = Client.query.join(Reseller, Client.reseller_id == Reseller.id) \
          .filter(Reseller.name == target_reseller) \
          .filter(Client.name.like('%' + by_name_query + '%') | Client.company.like('%' + by_name_query + '%'))\
          .offset(offset)\
          .limit(limit)\
          .all()
      else:  
        clients = Client.query.join(Reseller, Client.reseller_id == Reseller.id) \
            .filter(Reseller.name == target_reseller) \
            .offset(offset)\
            .limit(limit)\
            .all()          
    else:
      reseller = abort_if_obj_doesnt_exist(self.filter_by, target_reseller, Reseller)
      clients = Client.query.filter_by(id = self.request_user_client_id).first()
      clients = [clients]

    if not clients:
      abort(404, message = 'Could not find any clients')

    return {
      'response' : { 
        'reseller_id' : reseller.id,
        'clients' : ClientSchema(many=True).dump(clients).data
      } 
    }

class ClientResource(BaseResource):
  def __init__(self):
    filters = ['id', 'name']
    super(ClientResource, self).__init__(filters)

  @property
  def scope(self):
    return 'client'

  # Overrided
  def is_forbidden(self, target_reseller, target_client):
    """ Check for access rules:
    A global admin must not have any restrictions.
    A requester admin must create and delete clients
    A requester must have access to your own clients
    """
    if self.is_global_admin: return
    # Only admin can create and delete clients
    if not self.is_admin and not request.method in ['GET', 'PUT']:
      abort(403, message = 'Permission denied! Does not have enough permissions for access this resource')

    if not target_reseller:
      abort(400, message = 'Could not find "target_reseller"')

    reseller = abort_if_obj_doesnt_exist('name', target_reseller, Reseller)
    if self.request_user_reseller_id != reseller.id:
      abort(403, message = 'Permission denied! The reseller does not belongs to the requester.')

  def get(self, target_reseller, target_client):
    reseller = abort_if_obj_doesnt_exist('name', target_reseller, Reseller)
    client = abort_if_obj_doesnt_exist(self.filter_by, target_client, Client)
    client_result = ClientSchema().dump(client)
    return { 
      'response' : { 
        'client' : client_result.data 
      }
    }

  def delete(self, target_reseller, target_client):
    reseller = abort_if_obj_doesnt_exist('name', target_reseller, Reseller)
    client = abort_if_obj_doesnt_exist(self.filter_by, target_client, Client)
    if client.domain.all():
      abort(409, message = 'There are still domains associated with this client')
    db.session.delete(client)
    db.session.commit()
    return '', 204

  def put(self, target_reseller, target_client):
    abort_if_obj_doesnt_exist('name', target_reseller, Reseller)
    client = abort_if_obj_doesnt_exist('name', target_client, Client)

    # TODO: Validate email field
    self.parser.add_argument('email', type=str)
    self.parser.add_argument('phone', type=str)
    self.parser.add_argument('company', type=str)
    self.parser.add_argument('reseller_name', type=str)
    reqdata = self.parser.parse_args()

    # Check if the user belongs to the reseller
    client.email = reqdata.get('email') or client.email
    client.phone = reqdata.get('phone') or client.phone
    client.company = reqdata.get('company') or client.company

    print client.email, client.phone, client.company

    if reqdata.get('reseller_name'):
      if not self.is_global_admin:
        abort(403, message = 'Permission denied! Does not have enough permissions.')
      newreseller = Reseller.query.filter_by(name = reqdata.get('reseller_name')).first()
    else:
      newreseller = Reseller.query.filter_by(name = target_reseller).first()

    client.reseller_id = newreseller.id

    db.session.commit()
    return '', 204

  def post(self, target_reseller, target_client):
    target_client = target_client.lower()
    reseller = abort_if_obj_doesnt_exist('name', target_reseller, Reseller)
    if Client.query.filter_by(name=target_client).first():
      abort(409, message='The client {} already exists'.format(target_client))

    #sleep(2)
    # TODO: Validate email field
    self.parser.add_argument('email', type=str, required=True, case_sensitive=True)
    self.parser.add_argument('login_name', type=str)
    self.parser.add_argument('login', type=str, case_sensitive=True)
    self.parser.add_argument('password', type=str)
    self.parser.add_argument('company', type=str, required=True)
    self.parser.add_argument('enable_api', type=bool, default=False)
    self.parser.add_argument('admin', type=bool, default=False)
    reqdata = self.parser.parse_args()

    # Check if the user belongs to the reseller
    client = Client(
      reseller_id = reseller.id,
      name = target_client,
      email = reqdata['email'],
      company = reqdata['company']
    )

    if reqdata['login'] and reqdata['login_name'] and reqdata['password']:
      user = User(login = reqdata['login'],
        name = reqdata['login_name'],
        email = reqdata['email'],
        password = Security.hash_password(reqdata['password']),
        api_enabled = reqdata['enable_api'],
        admin = reqdata['admin']
      )
      client.user = [user]
    db.session.add(client)
    db.session.commit()
    client = Client.query.filter_by(name=target_client).one()

    return {
      'response' : { 
        'client_id' : client.id
      } 
    }, 201
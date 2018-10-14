import sys, logging.config, os, time, socket, yaml, ssl
from flask import (
  Flask, request, jsonify, app,
  make_response, Response, json, Blueprint
)
from werkzeug.exceptions import default_exceptions
from werkzeug.exceptions import HTTPException
from werkzeug.contrib.fixers import ProxyFix
from flask_cors import CORS
import flask_restful as restful
from traceback import format_exc

from jenova.components import db, create_app, Config, CallLogger
from jenova.resources import (
  # Resellers and clients resources
  ResellerListResource, ResellerDomainListResource, ResellerListByQueryResource, ResellerResource,
  ClientResource, ClientListResource, ResellerServicesListResource,

  # User, Auth, Scope and Permissions resources
  AuthenticationResource, PermissionsResource, UserListResource, UserChangeStateResource,
  UserResource, ScopeUserResource, ScopeListResource, ScopeListUserResource, ScopeResource,

  # Domain and Cos resources
  DomainResource, DomainServiceResource, DomainListResource,
  DomainServicePreAuthDelegationResource, CosResource, DomainServiceStateResource,
  DomainListServiceStateResource, DomainListByQueryResource, DomainCosResource,

  # Task resources
  TaskResource,

  # Services resources
  ServiceResource,

  # DNS resources
  DnsSOAResource, DnsRecordsResource, DnsRecordsBackupResource,

  # Notices resources
  NoticesResource,

  # Accounts resources
  ExternalAccountsResource, ExternalAccountsListResource, ExternalDomainStatusResource,

  # Reports resources
  ResellerReportResource, DomainReportResource,

  #Distribution List resources
  DistributionListsResource, DistributionListResource
)

SKEY = 'changeme'
logger = CallLogger.logger()

# TODO: Review tasks (create domain)
# TODO: More restful... Set Location header to conflicting problems
# TODO: disable reseller must inactivate all users
# TODO: Refactor http status code
# TODO: Change str parameters to unicode one
# TODO: all args must be strict! (Cannot accept unknown args!)
# TODO: Convert post params to lowercase
# TODO: create password strength
# TODO: Reject request headers != application/json
# TODO: Performance query adjustment: http://stackoverflow.com/questions/28280507/setup-relationship-one-to-one-in-flask-sqlalchemy

def is_dev():
  return os.environ.get('NODE_ENV') == 'development'

try:
  #app = flask_app
  app = create_app()
  CORS(app, expose_headers=['Location'])
  api = restful.Api(app)
  main_config = Config.load()

  # Resellers/Clients
  logging.config.dictConfig(main_config['logger'])
  logger = logging.getLogger(__name__)

  # Resellers/Clients resources
  api.add_resource(ResellerListResource, '/resellers')
  api.add_resource(ResellerListByQueryResource, '/resellers/<by_name_query>')
  api.add_resource(ResellerResource, '/resellers/<target_reseller>')
  api.add_resource(DomainListByQueryResource, *[
      '/clients/<client_name>/domains/<by_name_query>',
      '/resellers/<reseller_name>/domains/<by_name_query>',
      '/resellers/domains/<by_name_query>'
    ]
  )
  api.add_resource(ResellerDomainListResource, '/resellers/<target_reseller>/domains')
  api.add_resource(ResellerServicesListResource, '/resellers/<target_reseller>/services')

  api.add_resource(ClientListResource, *[
    '/clients',
    '/clients/<by_name_query>',
    '/resellers/<target_reseller>/clients',
    '/resellers/<target_reseller>/clients/<by_name_query>',
    ]
  )
  api.add_resource(ClientResource, '/resellers/<target_reseller>/clients/<target_client>')

  # User, resources
  # Users belong to reseller (may be created only in reseller creation) or clients.
  api.add_resource(UserListResource, *[
    '/users',
    '/users/<by_name_query>'
    ]
  )
  api.add_resource(UserResource, '/users/<target_auth>')
  api.add_resource(UserChangeStateResource, *[
      '/users/<target_auth>/globaladmin',
      '/users/<target_auth>/admin',
      '/users/<target_auth>/api'
    ]
  )

  # Notices Resource
  api.add_resource(NoticesResource, *[
      '/service/<target_service>/notices',
      '/service/<target_service>/notices/<notice_id>'
    ]
  )

  # Scopes/Permissions resources
  # http://api.inova.com.br:8080/scopes/dns/users/speedhost/permissions
  # Scopes are unique. Has users and permissions bound into it
  api.add_resource(ScopeListResource, '/scopes')
  api.add_resource(ScopeResource, '/scopes/<scope_name>')
  api.add_resource(ScopeListUserResource, '/scopes/<scope_name>/users')
  api.add_resource(ScopeUserResource, '/scopes/<scope_name>/users/<user>')
  api.add_resource(PermissionsResource, *[
      '/scopes/<scope_name>/users/<user>/permissions',
      '/scopes/<scope_name>/users/<user>/permissions/read',
      '/scopes/<scope_name>/users/<user>/permissions/write',
      '/scopes/<scope_name>/users/<user>/permissions/edit',
      '/scopes/<scope_name>/users/<user>/permissions/delete'
    ]
  )

  # Reports
  api.add_resource(ResellerReportResource, '/reports/resellers/<target_reseller>')
  api.add_resource(DomainReportResource, '/reports/domains/<target_domain>/services/<target_service>')
  # External Domain Status
  api.add_resource(ExternalDomainStatusResource, '/services/<service_name>/domains/<domain_name>/status')

  # External Accounts Management
  api.add_resource(ExternalAccountsResource, '/services/<service_name>/domains/<domain_name>/accounts/<target_account>')
  api.add_resource(ExternalAccountsListResource, '/services/<service_name>/domains/<domain_name>/accounts')
  
  #External Accounts -> DistributionListResource
  api.add_resource(DistributionListsResource, '/services/<service_name>/domains/<domain_name>/dlists')
  api.add_resource(DistributionListResource,  '/services/<service_name>/domains/<domain_name>/dlists/<dlist_name>')
  
  # Authentication resource
  api.add_resource(AuthenticationResource, *['/login', '/auth'])

  # Domain resources
  config_state = {  'main_config' : main_config }
  api.add_resource(DomainListResource, '/clients/<client_name>/domains')
  api.add_resource(DomainResource, '/clients/<client_name>/domains/<domain_name>')
  api.add_resource(DomainListServiceStateResource, '/clients/<client_name>/domains/<target_domain>/services')
  api.add_resource(DomainServiceResource, '/services/<service_name>/domains/<domain_name>', resource_class_kwargs = config_state)
  api.add_resource(DomainServiceStateResource, '/clients/<client_name>/domains/<target_domain>/services/<service_name>')

  # Task resources
  api.add_resource(TaskResource, '/tasks/<task_type>/id/<task_id>')

  api.add_resource(DomainServicePreAuthDelegationResource,
    '/services/<service_name>/domains/<domain_name>/preauth',
    resource_class_kwargs = config_state
  )
  api.add_resource(DomainCosResource, '/services/<service_name>/domains/<domain_name>/cos')
  domain_endpoints = ['/clients/<client_name>/domains/<domain_name>', '/domains/<target_domain>']

  # TODO: remake endpoints
  api.add_resource(ServiceResource, '/service/<target_service>')

  api.add_resource(CosResource, '/service/<service_name>/cos/<target_cos>')
  api.add_resource(DnsSOAResource, '/service/<service_name>/zone/<domain_name>')
  api.add_resource(DnsRecordsResource, *[
        '/service/<service_name>/zone/<domain_name>/type/<dns_type>/name/<name>',
        '/service/<service_name>/zone/<domain_name>/type/<dns_type>/name/<name>/content/<content>/ttl/<ttl>'
      ]
  )

  api.add_resource(DnsRecordsBackupResource, '/service/<service_name>/zone/<domain_name>/backup')  
  # TODO: dns_type get resource.
  # TODO: all domains resource

  if is_dev():
    while True:
      s = socket.socket()
      try:
        s.connect((os.environ['JNV_MDB_HOST'], 3306))
        break
      except Exception, e:
        # TODO: logger HERE
        print 'Error connecting to database, sleeping for 5 seconds...', e
        time.sleep(6)

  #db.init_app(app)
  if is_dev():
    with app.app_context():
      db.create_all()
      from jenova.models import User, UserSchema, Scope, Permissions
      from jenova.components import Security
      from datetime import datetime
      import jwt

      if not User.query.filter_by(login=os.environ.get('AUTH_LOGIN')).first():
        user = User(
          login = os.environ.get('AUTH_LOGIN'),
          name = 'QA Admin',
          email = 'sandro.mello@inova.net',
          password = Security.hash_password(os.environ.get('AUTH_PASSWORD')),
          api_enabled = True,
          global_admin = True
        )

        # for scope_name in DEFAULT_SCOPES:
        #   scope = Scope(name = scope_name)
        #   scope.permissions = Permissions(
        #     read = True,
        #     write = True,
        #     delete = True,
        #     edit = True
        #   )
        #   db.session.add(scope)
        #   db.session.commit()


        #plain_secretkey, hashed_secretkey = os.environ.get('SECRETKEY'), os.environ.get('HASHED_SECRETKEY')
        #user.api_access = [ApiAccess(api_key=os.environ.get('APIKEY'), secret_key=hashed_secretkey,
        #  comment='QA/DEV ADMIN USER')]
        db.session.add(user)
        db.session.commit()

        user = User.query.filter_by(login = os.environ.get('AUTH_LOGIN')).first()
        token = jwt.encode({'user' : UserSchema().dump(user).data}, SKEY, algorithm='HS256')
        logger.info(token)


  # support for wsgi containers: http://flask.pocoo.org/docs/0.10/deploying/wsgi-standalone/
  app.wsgi_app = ProxyFix(app.wsgi_app)

except KeyError, ex:
  print 'Could not find environment variable', ex
  sys.exit(1)
except Exception, ex:
  print 'Error doing the initial config: %s\n%s' % (ex, format_exc())
  sys.exit(1)

if __name__ == '__main__':
  try:
    if is_dev():
      app.run(host='0.0.0.0', port=8443, debug=True, threaded=True)
    context = ssl.SSLContext(ssl.PROTOCOL_SSLv23)
    context.load_cert_chain(os.environ.get('JNV_SSL_CERT'), os.environ.get('JNV_SSL_KEY'))
    app.run(host='0.0.0.0', port=8443, debug=True, ssl_context=context, threaded=True)

  except Exception, e:
    print 'Error starting web app: %s' % e

import json, requests
from .exceptions import mxheroError
from flask_restful import abort


class Mxhero(object):
  def __init__(self, environment, mxh_api):
    self.url = '%s/%s/domain' % (mxh_api, environment)
    self.HEADERS = {'Content-Type' : 'application/json'}

  def get_response(self, response):

    if response.status_code not in [200, 201, 204]:
      raise mxheroError(
        message = 'Error processing request. %s. code: %s' % (response.json()['message'], response.status_code),
        response = response
      )
    return response

  def create(self, domain_name):
    '''Create mxhero domain with defaults rules.
    :param str:domain - Domain name to create.
    :param str:environment - Environment Name (mxhcorp, mxlite, etc)

    Returns: Raise error if ocours.
    '''
    mxh_url = "%s/%s" % (self.url, domain_name)
    # TODO
    # Each zimbra service has to have a addon integration with mxhero.
    # Front end must give the ability for the admin to enable with default configurations
    # or manual settings.
    reqdata = json.dumps({
      'inbound_server': "mta-in.u.inova.com.br",
      'directory_type': "zimbra",
      'adsync_pass': "GPqZe2MCx",
      'adsync_port': "389",
      'adsync_host': "ldap.u.inova.com.br",
      'adsync_user': "uid=zimbra,cn=admins,cn=zimbra",
      'admin_email' : 'suporte@inova.com.br',
      'default_rules' : True
    })
    r = requests.put(mxh_url, data=reqdata, headers=self.HEADERS)
    return self.get_response(r)

  def delete(self, domain_name):
    '''Delete mxhero domain.
    :param str:domain - domain name to delete.
    :param str:environment - Environment Name (mxhcorp, mxlite, etc)

    Returns: Raise error if ocours
    '''
    mxh_url = "%s/%s" % (self.url, domain_name)
    r = requests.delete(mxh_url, headers=self.HEADERS)
    return self.get_response(r)
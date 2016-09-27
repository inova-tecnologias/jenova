from pythonzimbra.request_json import RequestJson
from pythonzimbra.communication import Communication
from pythonzimbra.request_xml import RequestXml
from pythonzimbra.tools.dict import get_value
from pythonzimbra.tools.auth import authenticate
from jenova.components.common import Config
from redis import StrictRedis
import json, time, random, string, re, pickle

class ZimbraRequestError(Exception):
  def __init__(self, message, response=None):
    """ Request Errors Zimbra
    :param message: Error message
    :param response: pythonzimbra.response.Response type
    """
    self.message = message
    self.response = response

    super(ZimbraRequestError, self).__init__(message)

class ZimbraRequest(object):
  def __init__(self, admin_url, admin_user, admin_pass):
    self.admin_url = admin_url
    self.admin_user = admin_user
    self.admin_pass = admin_pass
    self.admin_account_by = 'name'
    self.request = None

    self.token = authenticate(
      self.admin_url,
      self.admin_user,
      self.admin_pass,
      self.admin_account_by,
      admin_auth=True,
      request_type="json"
    )

    self.comm = Communication(self.admin_url)

  def searchDirectory(self, query, domain_name=None, count_only=False,
    types='accounts', offset=0, limit=0, attrs='zimbraId'):
    """ Search Zimbra Directory
    :param query: Query string - should be an LDAP-style filter string (RFC 2254)
    :param domain_name: The domain name to limit the search to. Default: Don't limit the search
    :param count_only: Whether response should be count only. Default: False
    :param types: Comma-separated values: accounts|distributionlists|aliases|resources|domains|coses. Default: accounts
    :param offset: Where does the request start. Default: 0
    :param limit: Limit of the request. Default: 50
    :param attrs: Comma separated list of attributes (zmprov desc). Default: zimbraId
    """
    # '1' true '0' false
    request_dict = {
      'query' : query,
      'countOnly' : count_only and '1' or '0',
      'types' : types,
      'offset' : offset,
      'limit' : limit,
    }

    if domain_name:
      request_dict['domain'] = domain_name
    if attrs:
      request_dict['attrs'] = attrs

    self.cleanUp()
    self.request.add_request(
      request_name = 'SearchDirectoryRequest',
      request_dict = request_dict,
      namespace = 'urn:zimbraAdmin'
    )

    response = self.comm.send_request(self.request)
    if response.is_fault():
      raise ZimbraRequestError(
        message = 'Error searching directory. Error: %s' % response.get_fault_message(),
        response = response
      )

    return response.get_response()

  def createDomain(self, domain_name, attrs=list()):
    """ Create a new domain
    :param domain_name: The name of the domain
    :param attrs: List of tuple attributes of domain (zmprov desc domain)
    """
    if not type(attrs) == list:
      raise TypeError('attrs must be a list')
    self.cleanUp()

    request_attrs = []
    for attr in attrs:
      zattr, value = attr
      request_attrs.append({
        'n' : zattr,
        '_nt' : value
      })

    request_dict = { 'name' : domain_name }
    if request_attrs:
      request_dict['a'] = request_attrs

    self.request.add_request(
      request_name = 'CreateDomainRequest',
      request_dict = request_dict,
      namespace = 'urn:zimbraAdmin'
    )

    response = self.comm.send_request(self.request)
    if response.is_fault():
      raise ZimbraRequestError(
        message = 'Error creating domain %s Error: %s' % (domain_name, response.get_fault_message()),
        response = response
      )

    return response.get_response()

  def setPassword(self, account_zimbra_id, password):
    """ Set a password for an zimbra account
    :param account: The target account name
    :param password: The clear text password you want to define.
    """
    self.cleanUp()

    self.request.add_request(
      request_name = "SetPasswordRequest",
      request_dict = {
        "id": {
          "_content": account_zimbra_id,
        },
        "newPassword" : {
          "_content" : password
        }
      },
      namespace = "urn:zimbraAdmin"
    )

    response = self.comm.send_request(self.request)
    if response.is_fault():
      raise ZimbraRequestError(
        message = 'Error setting account password %s. Error: %s' % (account_zimbra_id, response.get_fault_message()),
        response = response
      )

    return response.get_response()

  def deleteDomain(self, domain_id):
    """ Delete a domain
    :param domain_id: The zimbraId of the domain
    """
    self.cleanUp()

    self.request.add_request(
      request_name = 'DeleteDomainRequest',
      request_dict = { 'id' : domain_id },
      namespace = 'urn:zimbraAdmin'
    )

    response = self.comm.send_request(self.request)
    if response.is_fault():
      raise ZimbraRequestError(
        message = 'Error deleting domain %s Error: %s' % (domain_id, response.get_fault_message()),
        response = response
      )

    return response.get_response()

  def getDomain(self, domain, attrs):
    """
    TODO
      - Implement list of attributes to get.
      - Raise exception when an error occours.
    """

    """
    Returns the attributes requested in a json format.
     "GetDomainResponse": {
      "domain": {
        "a": [
      {
         "_content": "externalLdapAutoComplete",
         "n": "zimbraGalAutoCompleteLdapFilter"
      },
      {
         "_content": "FALSE",
         "n": "zimbraAdminConsoleDNSCheckEnabled"
      },
      .
      .
      .
      https://files.zimbra.com/docs/soap_api/8.6.0/api-reference/zimbraAdmin/GetDomain.html
    """
    if not type(attrs) == list:
      raise TypeError('attrs must be a list')
    self.cleanUp()

    attrs =  ','.join(attrs)
    self.request.add_request(
      request_name = "GetDomainRequest",
      request_dict = {
        "attrs" : attrs,
        "domain": {
          "_content" : domain,
          "by": "name",
        },
      },
      namespace = "urn:zimbraAdmin"
    )

    response = self.comm.send_request(self.request)
    if response.is_fault():
      raise ZimbraRequestError(
        message = 'Error getting domain %s Error: %s' % (domain, response.get_fault_message()),
        response = response
      )

    return response.get_response()

  def modifyDomain(self, domain_id, attrs):
    """ Modify an attribute of a domain
    This method is idempotent, it will not change the result executing multiple times
    :param domain_id: The zimbraID of the domain
    :param attrs: A list of tuple containing the zimbra attribute with the corresponding value: [(zattr, value), ...]
    """
    self.cleanUp()

    request_attrs = []
    for attr in attrs:
      zattr, value = attr
      request_attrs.append({
        'n' : zattr,
        '_content' : value
      })

    self.request.add_request(
      request_name = "ModifyDomainRequest",
      request_dict = {
        "id": {
          "_content": domain_id,
        },
        "a": request_attrs
      },
      namespace = "urn:zimbraAdmin"
    )

    response = self.comm.send_request(self.request)
    if response.is_fault():
      raise ZimbraRequestError(
        message = 'Error modifying domain %s Error: %s' % (domain_id, response.get_fault_message()),
        response = response
      )

    return response.get_response()

  def getAccount(self, account, attrs=[]):
    """
    TODO
      - Implement a list of attributes to get.
    """


    """
    Returns a json containing all attributes of an account or an exception:

    "GetAccountResponse": {
      "account": {
   "a": [
    {
       "_content": "FALSE",
       "n": "zimbraPrefCalendarReminderMobile"
    },
          {
        "_content": "TRUE",
        "n": "zimbraPrefIMLogChats"
    },
    .
    .
    .

    https://files.zimbra.com/docs/soap_api/8.6.0/api-reference/zimbraAdmin/GetAccount.html
    """
    if not type(attrs) == list:
      raise TypeError('attrs must be a list')

    attrs =  ','.join(attrs)

    self.cleanUp()

    self.request.add_request(
      "GetAccountRequest",
      {
        "attrs" : attrs,
        "account": {
          "_content": account,
          "by": "name",
        },
      },
      "urn:zimbraAdmin"
    )

    response = self.comm.send_request(self.request)

    if response.is_fault():
      raise ZimbraRequestError("Reponse failed: (%s) %s" % (response.get_fault_code(), response.get_fault_message()))
    return(response.get_response())

  def getAccountId(self, account_name):
    """ Return a string with account zimbraId
    :param account_name:str name of the zimbra account.
    """
    res = self.getAccount(account=account_name, attrs=['zimbraId'])
    return res['GetAccountResponse']['account']['id']


  def createAccount(self, account, password=None, attrs=list()):
    """ Create a new account into Zimbra system
    :param account: The target account
    :param password: The given for the account
    :param attrs: A list of tuple containing the zimbra attribute with the corresponding value: [(zattr, value), ...]
    """
    request_attrs = []
    for attr in attrs:
      zattr, value = attr
      request_attrs.append({
        'n' : zattr,
        '_content' : value
      })

    self.cleanUp()

    if not password:
      password = 'xr%nUojEbm^Tj3c7QfTa*c04G%0g*jE1$o1nGcBT'

    self.request.add_request(
      request_name = "CreateAccountRequest",
      request_dict = {
        "name" : account,
        "password" : password,
        "a" : request_attrs
      },
      namespace = "urn:zimbraAdmin"
    )

    response = self.comm.send_request(self.request)
    if response.is_fault():
      raise ZimbraRequestError(
        message = 'Error creating account %s. Error: %s' % (account, response.get_fault_message()),
        response = response
      )

    return response.get_response()

  def modifyAccount(self, account_zimbra_id, attrs=list()):
    """ Modify an account into Zimbra system
    :param account_zimbra_id: The target account zimbraId
    :param attrs: A list of tuple containing the zimbra attribute with the corresponding value: [(zattr, value), ...]
    """
    request_attrs = []
    for attr in attrs:
      zattr, value = attr
      request_attrs.append({
        'n' : zattr,
        '_content' : value
      })

    self.cleanUp()

    self.request.add_request(
      request_name = "ModifyAccountRequest",
      request_dict = {
        "id": {
          "_content": account_zimbra_id,
        },
        "a" : request_attrs
      },
      namespace = "urn:zimbraAdmin"
    )

    response = self.comm.send_request(self.request)
    if response.is_fault():
      raise ZimbraRequestError(
        message = 'Error modifying account %s. Error: %s' % (account_zimbra_id, response.get_fault_message()),
        response = response
      )

    return response.get_response()

  def cleanUp(self):
    """ Clean up after one step to leave a dedicated result for the other
     test cases.
    """
    self.setUp()

  def setUp(self):
    """
    Setup everything required to make a request to zimbra server.
    """

    self.request = RequestJson()
    self.request = self.comm.gen_request(token=self.token)

  def getDomainId(self, domain):
    """
    Returns the zimbraId of a domain. Useful to modify a domain with ModifyDomainRequest for instance.

    domain_id = self.getDomainId(inova.net)
    equal:
    domain_id = '4af850c7-7e44-452e-ad25-c70fda58f9bf'
    """
    self.cleanUp()

    self.request.add_request(
      "GetDomainInfoRequest",
      {
        "domain": {
          "_content": domain,
          "by": "name",
        },
      },
      "urn:zimbraAdmin"
    )

    response = self.comm.send_request(self.request)

    if response.is_fault():
      raise ZimbraRequestError("Reponse failed: (%s) %s" % (response.get_fault_code(), response.get_fault_message()))

    return response.get_response()['GetDomainInfoResponse']['domain']['id']

  def getDomainQuotaUsage(self,domain):
    """
     Returns quota usage of all users of a specific domain
      {
      "GetQuotaUsageResponse": {
       "searchTotal": 1294,
       "account": [
        {
        "used": 0,
    "limit": 0,
    "name": "santoro2701.gmail.com@conbras.com",
    "id": "63b128d6-b7f2-466d-ac86-7b253e62a7ed"
     },
     {
    "used": 28,
    "limit": 26843545600,
    "name": "dp.rio@conbras.com",
    "id": "5b4832d1-b642-4778-ab7d-3056ebcefada"
     },
    .
    .
    .
      https://files.zimbra.com/docs/soap_api/8.6.0/api-reference/zimbraAdmin/GetQuotaUsage.html

    """
    self.cleanUp()

    self.request.add_request(
        "GetQuotaUsageRequest",
        {
            "domain": domain,
      "allServers": "1",
      "sortBy": "percentUsed",
      "sortAscending": "1",
        },
        "urn:zimbraAdmin"
    )

    response = self.comm.send_request(self.request)

    if response.is_fault():
      raise ZimbraRequestError("Reponse failed: (%s) %s" % (response.get_fault_code(), response.get_fault_message()))

    return(response.get_response())

  def getCos(self, cos_name):
    """ Get COS by it's name
    :param cos_name: The name of the COS
    """
    self.cleanUp()
    self.request.add_request(
      request_name = 'GetCosRequest',
      request_dict = {
        'cos' : {
          'by' : 'name',
          '_content' : cos_name
        }
      },
      namespace = 'urn:zimbraAdmin'
    )
    response = self.comm.send_request(self.request)
    if response.is_fault():
      raise ZimbraRequestError(
        message = 'Error getting COS %s. Error: %s' % (cos_name, response.get_fault_message()),
        response = response
      )

    return response.get_response()

  def createCos(self, cos_name, features=dict()):
    """ Create a new cos.
    :param cos_name: The name of the COS
    :param features: A dict representing the feature->value
    """
    if type(features) is not dict:
      raise TypeError('Wrong type found for features, must be a dict.')

    features_req = []
    for feature, value in features.items():
      features_req.append({
        'n' : feature ,
        '_content' : value
      })

    self.cleanUp()
    self.request.add_request(
      request_name = 'CreateCosRequest',
      request_dict = {
        'name' : { '_content' : cos_name },
        'a' : features_req
      },
      namespace = 'urn:zimbraAdmin'
    )

    response = self.comm.send_request(self.request)
    if response.is_fault():
      raise ZimbraRequestError(
        message = 'Error creating COS %s Error: %s' % (cos_name, response.get_fault_message()),
        response = response
      )

    return response.get_response()

  def modifyCos(self, zimbra_cos_id, features):
    """ Update a cos.
    :param zimbra_cos_id: The zimbraID of the COS
    :param features: A dict representing the feature->value
    """
    if type(features) is not dict:
      raise TypeError('Wrong type found for features, must be a dict')

    features_req = []
    for feature, value in features.items():
      features_req.append({
        'n' : feature,
        '_content' : value
      })

    self.cleanUp()
    self.request.add_request(
      request_name = 'ModifyCosRequest',
      request_dict = {
        'id' : {
          '_content' : zimbra_cos_id
        },
        'a' : features_req
      },
      namespace = 'urn:zimbraAdmin'
    )

    response = self.comm.send_request(self.request)
    if response.is_fault():
      raise ZimbraRequestError(
        message = 'Error creating COS %s Error: %s' % (cos_name, response.get_fault_message()),
        response = response
      )

    return response.get_response()

  def deleteCos(self, zimbra_cos_id):
    """ Delete a specific COS
    :param zimbra_cos_id: The zimbraID of the COS
    """
    self.cleanUp()
    self.request.add_request(
      request_name = 'DeleteCosRequest',
      request_dict = {
        'id' : { '_content' : zimbra_cos_id }
      },
      namespace = 'urn:zimbraAdmin'
    )
    response = self.comm.send_request(self.request)

    if response.is_fault():
      raise ZimbraRequestError(
        message = 'Error creating COS %s Error: %s' % (cos_name, response.get_fault_message()),
        response = response
      )

    return response.get_response()

  def getComputeAggregateQuotaUsage(self):
    """ This method get all quota usage of all domains in a zimbra system. This may take a while depending how many domains and servers you have. Use wisely :P
    """

    self.cleanUp()

    self.request.add_request(
        "ComputeAggregateQuotaUsageRequest",
        {
        },
        "urn:zimbraAdmin"
    )

    response = self.comm.send_request(self.request)

    if response.is_fault():
      raise ZimbraRequestError("Reponse failed: (%s) %s" % (response.get_fault_code(), response.get_fault_message()))

    return(response.get_response())

  def getDistributionListId(self, dlist_name):
    """ Return a string with dlist zimbraId
    :param dlist_name:str name of the zimbra dlist.
    """
    res = self.getDistributionList(dlist_name)
    return res['GetDistributionListResponse']['dl']['id']

  def createDistributionList(self, dlist, attrs=list()):
    """ This method create zimbra distribution list. A list of attributes may be given, we will handle it for you.
    :param dlist: The target distribution list
    :param attrs: List of tuple attributes of distribution list
    """
    if not type(attrs) == list:
      raise TypeError('attrs must be a list')

    request_attrs = []
    for attr in attrs:
      zattr, value = attr
      # If it's a list, then it's a multi-value attribute
      if type(value) == list:
        for multi_attr in value:
          request_attrs.append({
            'n' : zattr,
            '_content' : multi_attr
          })
      else:
        request_attrs.append({
          'n' : zattr,
          '_content' : value
        })
    self.cleanUp()

    self.request.add_request(
      request_name = "CreateDistributionListRequest",
      request_dict = {
        "name": dlist,
        "a": request_attrs
      },
      namespace = "urn:zimbraAdmin"
    )

    response = self.comm.send_request(self.request)
    if response.is_fault():
      raise ZimbraRequestError(
        message = 'Error creating DL %s Error: %s' % (dlist, response.get_fault_message()),
        response = response
      )

    return response.get_response()

  def getAllDistributionList(self, domain_name):
    """ Gets all distribution list in domain.
    """
    self.cleanUp()

    self.request.add_request(
      request_name = "GetAllDistributionListsRequest",
      request_dict = {
        "domain": {
          "by": "name",
          "_content" : domain_name
        },
      },
      namespace = "urn:zimbraAdmin"
    )

    response = self.comm.send_request(self.request)
    if response.is_fault():
      raise ZimbraRequestError(
        message = 'Error getting all DL: %s Error: %s' % (response.get_fault_message()),
        response = response
      )

    return response.get_response()

  def getDistributionList(self, dlist):
    """ Gets information about a distribution list.
    :param dlist: The target distribution list
    Obs: Tested with "a" attribute does not have effect on result
    """
    self.cleanUp()

    self.request.add_request(
      request_name = "GetDistributionListRequest",
      request_dict = {
        "dl": {
          "_content": dlist,
          "by": "name",
        },
      },
      namespace = "urn:zimbraAdmin"
    )

    response = self.comm.send_request(self.request)
    if response.is_fault():
      raise ZimbraRequestError(
        message = 'Error getting DL: %s Error: %s' % (dlist, response.get_fault_message()),
        response = response
      )

    return response.get_response()

  def deleteDistributionList(self, dlist_zimbra_id):
    """ Deletes distribution list
    :param dlist_zimbra_id: Distribution List zimbraID
    """
    self.cleanUp()
    self.request.add_request(
      request_name = "DeleteDistributionListRequest",
      request_dict = { "id" : dlist_zimbra_id },
      namespace = "urn:zimbraAdmin"
    )

    response = self.comm.send_request(self.request)
    if response.is_fault():
      raise ZimbraRequestError(
        message = 'Error deleting DL: %s Error: %s' % (dlist, response.get_fault_message()),
        response = response
      )
    return response.get_response()

  def addDistributionListMember(self, dlist_zimbra_id, members):
    """ This method adds members to a zimbra distribution list. A list of members must be sent.
    This method is idempotent, it will not change the result executing multiple times
    :param dlist_zimbra_id: The target distribution list zimbraId
    :param members: List containing the account members
    """
    if not type(members) == list:
      raise TypeError('members must be a list')

    zmembers = []
    for member in members:
      zmembers.append({'_content': member})

    self.cleanUp()

    self.request.add_request(
      request_name = "AddDistributionListMemberRequest",
      request_dict = {
        "id": dlist_zimbra_id,
        "dlm": zmembers
      },
      namespace = "urn:zimbraAdmin"
    )

    response = self.comm.send_request(self.request)
    if response.is_fault():
      raise ZimbraRequestError(
        message = 'Error adding members to dlist %s Error: %s' % (dlist_zimbra_id, response.get_fault_message()),
        response = response
      )

    return response.get_response()

  def removeDistributionListMember(self, dlist_zimbra_id, members):
    """ This method removes members from a zimbra distribution list. A list of members must be sent.
    :param dlist_zimbra_id: The target distribution list zimbraId
    :param members: List containing the account members
    """
    if not type(members) == list:
      raise TypeError('members must be a list')

    zmembers = []
    for member in members:
      zmembers.append({'_content': member})
    self.cleanUp()

    self.request.add_request(
      request_name = "RemoveDistributionListMemberRequest",
      request_dict = {
        "id": dlist_zimbra_id,
        "dlm": zmembers
      },
      namespace = "urn:zimbraAdmin"
    )

    response = self.comm.send_request(self.request)
    if response.is_fault():
      raise ZimbraRequestError(
        message = 'Error removing members to dlist %s Error: %s' % (dlist_zimbra_id, response.get_fault_message()),
        response = response
      )

    return response.get_response()

  def grantRight(self, target_name, target_type, grantee_name, grantee_type, right, deny=0):
    """ Grant a right on a target to an individual or group grantee.
    This method is idempotent, it will not change the result executing multiple times
    :param target_name: The target for applying the right (by name). E.g.: 'inova.net'. External docs: /target
    :param target_type: The type of the target. E.g.: 'domain'. External docs: /target@type
    :param grantee_name: Grantee selector. E.g.: 'grp', 'dlist'. External docs: /grantee
    :param grantee_type: The type of the grantee. E.g.: 'grp', 'dlist'. External docs: /grantee@type
    :param right: The name of the right. E.g.: getDomainQuotaUsage, domainAdminConsoleRights. External docs: /right
    :param deny: Either to deny or grant the permission. Default is 0. External docs: /right@deny

    Ref. Docs: https://files.zimbra.com/docs/soap_api/8.6.0/api-reference/zimbraAdmin/GrantRight.html
    """
    self.cleanUp()
    self.request.add_request(
      request_name = "GrantRightRequest",
      request_dict = {
        "target": {
          "type": target_type,
          "by": "name",
          "_content": target_name
        },
        "grantee": {
          "type": grantee_type,
        "by": "name",
          "_content": grantee_name
        },
        "right": {
        "_content": right,
        "deny": deny
        }
      },
      namespace = "urn:zimbraAdmin"
    )
    response = self.comm.send_request(self.request)
    if response.is_fault():
      raise ZimbraRequestError(
        message = 'Error adding grant to target_name %s Error: %s' % (target_name, response.get_fault_message()),
        response = response
      )

    return response.get_response()


  def getQuotaUsage(self, target_server_id=None):
    """ This method retrieves quota from each account hosted in a specific server.
    :param target_server_id:str - zimbraId of an mbox server.

    if target_server_id is not specified it will retrieve mboxes where the connection
    was estabilished. Ideal for single server instalations, otherwise consider to use inside
    a loop with all mailbox servers.

    """
    self.cleanUp()

    if target_server_id:
      self.request.set_context_params({
        'targetServer' : {
          '_content' : target_server_id
        }
      })

    self.request.add_request(
      "GetQuotaUsageRequest",
      {
        # "domain": domain,
        "allServers": "1",
        "sortBy": "percentUsed",
        "sortAscending": "1",
      },
      "urn:zimbraAdmin"
    )

    response = self.comm.send_request(self.request)

    if response.is_fault():
      raise ZimbraRequestError("Reponse failed: (%s) %s" % (response.get_fault_code(), response.get_fault_message()))

    return(response.get_response())

  def getAllServers(self):
    """ Gets all servers from Zimbra.
    """
    self.cleanUp()

    self.request.add_request(
      "GetAllServersRequest",
      {},
      "urn:zimbraAdmin"
    )

    response = self.comm.send_request(self.request)

    if response.is_fault():
      raise ZimbraRequestError("Reponse failed: (%s) %s" % (response.get_fault_code(), response.get_fault_message()))

    return(response.get_response())

  def getAllCos(self):
    """ Get All COS
    """
    self.cleanUp()

    self.request.add_request(
      "GetAllCosRequest",
      {},
      "urn:zimbraAdmin"
    )

    response = self.comm.send_request(self.request)

    if response.is_fault():
      raise ZimbraRequestError("Reponse failed: (%s) %s" % (response.get_fault_code(), response.get_fault_message()))

    return(response.get_response())

  def getLdapEntries(self, query):
    """ Ldapsearch
    :param query:string Query string. Should be an Ldap style filter string(RFC 2254)
    """
    self.cleanUp()
 
    self.request.add_request(
      request_name = "GetLDAPEntriesRequest",
      request_dict = {
        "ldapSearchBase": {
          "_content": ""
        },
        "query": query
      },
      namespace = "urn:zimbraAdmin" 
    )

    response = self.comm.send_request(self.request)
    if response.is_fault():
      raise ZimbraRequestError(
        message = 'Error querying LDAP. Error: %s' % (response.get_fault_message()),
        response = response
      )
    return response.get_response()

  def countAccount(self, domain, by="name"):
    """ Count accounts on each COS for a defined domain
    :param domain:string Domain name.
    """
    self.cleanUp()
 
    self.request.add_request(
      request_name = "CountAccountRequest",
      request_dict = {
        "domain": {
          "_content" : domain,
          "by" : by
        }
      },
      namespace = "urn:zimbraAdmin"
    )

    response = self.comm.send_request(self.request)
    if response.is_fault():
      raise ZimbraRequestError(
        message = 'Error counting accounts. Error: %s' % (response.get_fault_message()),
        response = response
      )
    return response.get_response()
  
  
  def getCOSAccountUsage(self, domain_name):
    """ Compute account usage by COS.
    :param domain_name:str Domain Name.

    Returns: array of objects where each one is a cos with its limits and current account usage.
    [
      {"users": "1", "limit": "100", "id": "841b8df9-7406-4231-bb7b-175959ff8f9b", "name": "ProfessionalEdition"},
      {"users": "1", "limit": "200", "id": "8e97e282-8aa0-4ac4-96fb-7e2e7620c0a4", "name": "EmailEdition" }
    ]
    """

    rdata = [] 
    # get COS Limits on Domain
    limit = {}
    res = self.getDomain(domain_name, ['zimbraDomainCOSMaxAccounts'])
    if res['GetDomainResponse']['domain'].get('a'):
      l = res['GetDomainResponse']['domain']['a']
      if type(l) is not list: # turn into list if not
        l = [l]
      for cos in l:
        zid, num = cos['_content'].split(':')
        name = self.getCOS(by='id', cos=zid)['name']
        cos = {
          'users' : 0,
          'limit' : num,
          'id' : zid,
          'name' : name
        }
        rdata.append(cos)

    # get COS Account Count
    res = self.countAccount(domain=domain_name)
    if res['CountAccountResponse'].get('cos'):
      coss = res['CountAccountResponse']['cos']
      if type(coss) is not list: # turn into list if not
        coss = [coss]
      
      for cos in coss:
        cos['users'] = cos.pop('_content')
        cos_not_limited = True
        for c in rdata:
          if c['id'] == cos['id']:
            rdata.remove(c)
            c['users'] = cos['users']
            c['name'] = cos['name']
            rdata.append(c)
            cos_not_limited = False
        if cos_not_limited:
          cos['limit'] = 0
          rdata.append(cos)

    return rdata
  
  def getAllAccounts(self, domain_name, by="name"):
    """ Get accounts from a defined domain
    :param domain_name:string Domain name.
    :param by:string ['name', 'id']
    """
    self.cleanUp()
 
    self.request.add_request(
      request_name = "GetAllAccountsRequest",
      request_dict = {
        "domain": {
          "_content" : domain_name,
          "by" : by
        }
      },
      namespace = "urn:zimbraAdmin"
    )

    response = self.comm.send_request(self.request)
    if response.is_fault():
      raise ZimbraRequestError(
        message = 'Error counting accounts. Error: %s' % (response.get_fault_message()),
        response = response
      )
    return response.get_response()

  def getCOS(self, cos, by="name"):
    """Get COS Info. **Use cache**
    :param cos:string [cos name|cos id] depending of by param
    :param by:string [name|id]
    TODO # :param cached:bool use cached information or not. Faster when True.
    """
    self.cleanUp()
 
    
    self.request.add_request(
      request_name = "GetCosRequest",
      request_dict = {
        "cos": {
          "_content" : cos,
          "by" : by
        }
      },
      namespace = "urn:zimbraAdmin"
    )
    
    response = self.comm.send_request(self.request)
    if response.is_fault():
      raise ZimbraRequestError(
        message = 'Error getting COS info. Error: %s' % (response.get_fault_message()),
        response = response
      )
    return response.get_response()['GetCosResponse']['cos']

  def deleteAccount(self, zid, force=True):
    """delete an account.
    :param zid:string zimbra id of the account.
    """
    self.cleanUp()

    if force:
      self.request.add_request(
        request_name = "DeleteAccountRequest",
        request_dict = {
          "id": zid
        },
        namespace = "urn:zimbraAdmin"
      )
    
    response = self.comm.send_request(self.request)
    if response.is_fault():
      raise ZimbraRequestError(
        message = 'Error deleting account. Error: %s' % (response.get_fault_message()),
        response = response
      )
    return response.get_response()
  
  def renameAccount(self, zid, new_name):
    """delete an account.
    :param zid:string zimbra id of the account.
    :param new_name:string account new name
    """
    self.cleanUp()
    self.request.add_request(
        request_name = "RenameAccountRequest",
        request_dict = {
          "id": zid,
          "newName" : new_name
        },
        namespace = "urn:zimbraAdmin"
      )

    response = self.comm.send_request(self.request)
    if response.is_fault():
      domain = re.findall(r"no such domain: (.+?)$", response.get_fault_message())[0]
      if domain:
        self.createDomain(domain_name=domain)
        self.renameAccount(zid=zid, new_name=new_name)
      else:
        raise ZimbraRequestError(
          message = 'Error renaming account. Error: %s' % (response.get_fault_message()),
          response = response
        )
      
    return response.get_response()

  def getAccountMembership(self, account, by="name"):
    """ Get Distribution Lists an user belongs.
    :param account:str The key used to identify the account. Meaning determined by {acct-selector-by}
    :param by:str Type:adminName|appAdminName|id|foreignPrincipal|name|krb5Principal
                  Select the meaning of {acct-selector-key}
    """
    self.cleanUp()
    self.request.add_request(
        request_name = "GetAccountMembershipRequest",
        request_dict = {
          "account": {
            "_content" : account,
            "by" : by
          }
        },
        namespace = "urn:zimbraAdmin"
      )

    response = self.comm.send_request(self.request)
    if response.is_fault():
      raise ZimbraRequestError(
        message = 'Error getting distribution lists from account. Error: %s' % (response.get_fault_message()),
        response = response
      )
    return response.get_response()

class ZimbraReport(object):
  def __init__(self, admin_url, admin_user, admin_pass):
    self.zr = ZimbraRequest(
      admin_url = admin_url, 
      admin_user = admin_user, 
      admin_pass = admin_pass
    )
    config = Config.load()
    self.zimbra_edition_attributes = config['zimbra_edition_attributes']
    self.zimbra_edition_hierarchy = config['zimbra_edition_hierarchy']
    self.zimbra_report_plus_attributes = config['zimbra_report_plus_attributes']
    self.redis = StrictRedis(config['redishost'] or 500)
    self.report_cache_time = config['report_cache_time']

    self.all_edition_zattrs = []
    for edition in self.zimbra_edition_attributes.items():
        for attr in edition[1]:
          self.all_edition_zattrs.append(attr)
    self.all_edition_zattrs =  ','.join(self.all_edition_zattrs)
    
    # retrieve data from cache. it reduces significantly time.
    try:
      self.zcos = pickle.loads(self.redis.get('jenova:cache:zcos'))
      self.zdomain_cos = pickle.loads(self.redis.get('jenova:cache:zdomains'))
    except:
      pass
    

  def struct_search_result(self, types, data):
    '''
    param: types:str [cos|account|domain]
    param: data:dict - ZimbraRequest.searchDirectory response.
    '''

    r = {}
    
    if data['SearchDirectoryResponse']['searchTotal'] == 0:
      return r
    if type(data['SearchDirectoryResponse'][types]) != list:
      data['SearchDirectoryResponse'][types] = [data['SearchDirectoryResponse'][types]]

    for d in data['SearchDirectoryResponse'][types]:
      r[d['name']] = {'zimbraId' : d['id']}
      for a in d['a']:
        r[d['name']][a.get('n')] = a.get('_content') 

    return r
    
  def getEditionReport(self, domains, nocache=False):
    if 'zcos' not in dir(self) or nocache:
      self.zcos = self.struct_search_result(
        types = 'cos',
        data = self.zr.searchDirectory(
          query='objectClass=zimbraCOS', 
          types='coses',
          attrs=self.all_edition_zattrs))
      with self.redis.pipeline() as pipe:
        pipe.set('jenova:cache:zcos', pickle.dumps(self.zcos))
        pipe.expire('jenova:cache:zcos', self.report_cache_time)
        pipe.execute()
    
    if 'zdomain_cos' not in dir(self) or nocache:
      dcfilter = 'zimbraDomainDefaultCOSId=*'
      self.zdomain_cos = self.struct_search_result(
        types = 'domain',
        data = self.zr.searchDirectory(
          query=dcfilter,
          types='domains',
          attrs='zimbraDomainDefaultCOSId'))
      
      with self.redis.pipeline() as pipe:
        pipe.set('jenova:cache:zdomains', pickle.dumps(self.zdomain_cos))
        pipe.expire('jenova:cache:zdomains', 500)
        pipe.execute()
      
    zmda = '(|'
    for d in domains:
      zmda = zmda + '(zimbraMailDeliveryAddress=*@%s)' % d
    zmda = zmda + ')' 
    sfilter = '(&(objectClass=zimbraAccount)%s(!(objectClass=zimbraCalendarResource))(!(zimbraIsSystemResource=TRUE)))' % zmda
    self.zaccounts = self.struct_search_result(
      types = 'account',
      data = self.zr.searchDirectory(
          query=sfilter,
          types='accounts',
          attrs=','.join(self.zimbra_report_plus_attributes) + ',zimbraCOSId,' + self.all_edition_zattrs))

    cos_map = {}
    for cos in self.zcos:
      cos_map[self.zcos[cos]['zimbraId']] = cos
    
    
    rdata = {'response' : [], 'domains' : {}}
    
    for usr in self.zaccounts:
      
      domain_name = usr.split('@')[1]
      cos = 'default'

      # get cos
      if self.zdomain_cos.get(domain_name):
        default_domain_cos = self.zdomain_cos.get(domain_name)['zimbraDomainDefaultCOSId']
        cos = cos_map.get(default_domain_cos) or cos
      
      account_cos = self.zaccounts[usr].get('zimbraCOSId')
      if account_cos:
        cos = cos_map.get(account_cos) or cos
      
      # get edition
      edition_found = False
      for edition in self.zimbra_edition_hierarchy:
        for attribute in self.zimbra_edition_attributes[edition]:
          if not edition_found:
            if attribute in self.zaccounts[usr] and self.zaccounts[usr][attribute] == 'TRUE':
              edition_found = edition
            elif attribute in self.zcos[cos] and self.zcos[cos][attribute] == 'TRUE':
              edition_found = edition
      
      # get plus attributes
      for a in self.zimbra_report_plus_attributes:
        if not rdata['domains'].get(domain_name):
          rdata['domains'][domain_name] = {
            'editions' : {},
            'accounts' : {},
            'domain' : domain_name
          }
        if not rdata['domains'][domain_name]['accounts'].get(usr):
          rdata['domains'][domain_name]['accounts'][usr] = {'cos' : cos}
        rdata['domains'][domain_name]['accounts'][usr][a] = self.zaccounts[usr].get(a) 
      
        if not rdata['domains'][domain_name]['editions'].get(edition_found):
          rdata['domains'][domain_name]['editions'][edition_found] = 0
          
      
      # increment counter
      rdata['domains'][domain_name]['editions'][edition_found] += 1
    
    # sort data
    for d in rdata['domains']:
      accounts = []
      for a in rdata['domains'][d]['accounts']:
        rdata['domains'][d]['accounts'][a]['email'] = a
        accounts.append(rdata['domains'][d]['accounts'][a])
      
      rdata['domains'][d]['accounts'] = accounts
      rdata['response'].append(rdata['domains'][d])

    rdata.pop('domains')
    # end sort data

    return rdata['response']
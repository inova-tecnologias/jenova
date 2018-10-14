from flask_restful import abort
from datetime import datetime
from time import sleep

from jenova.resources.base import BaseResource, abort_if_obj_doesnt_exist
from jenova.models import Notices, NoticesSchema, Service
from jenova.components import db

class NoticesResource(BaseResource):
  def __init__(self):
    filters = ['id', 'name']
    super(NoticesResource, self).__init__(filters)

  def get(self, target_service, notice_id=''):   
    query = { 'service_name' : target_service }
    result = Notices.query.filter_by(**query).all()
    #result = Notices.query.all()
    return { 'response': NoticesSchema(many=True).dump(result).data }

  def delete(self, target_service, notice_id):
    notice = Notices.query.filter_by(id=notice_id).one()
    db.session.delete(notice)
    db.session.commit()
    return '', 204
  # TODO update method
  # def put(self, target_service, notice_id=''):

  def post(self, target_service, notice_id=''):
    service = abort_if_obj_doesnt_exist(self.filter_by, target_service, Service)    

    self.parser.add_argument('author', type=str, required=True)
    self.parser.add_argument('started_at', type=str)
    self.parser.add_argument('ended_at', type=str)
    self.parser.add_argument('notice_type', type=str)
    self.parser.add_argument('description', type=str)
    self.parser.add_argument('sla_impact', type=float)

    reqdata = self.parser.parse_args()

    notice = Notices(service_name = target_service,
      author = reqdata['author'],
      started_at = reqdata['started_at'],
      ended_at = reqdata.get('ended_at'),
      notice_type = reqdata.get('notice_type'),
      description = reqdata.get('description'),
      sla_impact = reqdata.get('sla_impact')
    )

    db.session.add(notice)
    db.session.commit()
    return {
      'response' : 'successfully created'
    }, 201
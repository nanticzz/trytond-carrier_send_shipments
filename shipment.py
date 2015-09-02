# This file is part of the carrier_send_shipments module for Tryton.
# The COPYRIGHT file at the top level of this repository contains
# the full copyright notices and license terms.
from datetime import datetime
from trytond.model import ModelView, fields
from trytond.wizard import Wizard, StateTransition, StateView, Button, \
    StateAction
from trytond.pool import Pool, PoolMeta
from trytond.transaction import Transaction
from trytond.pyson import Bool, Eval, Not
import logging
import tarfile
import tempfile

__all__ = ['Configuration', 'ShipmentOut', 'CarrierSendShipmentsStart',
    'CarrierSendShipmentsResult', 'CarrierSendShipments',
    'CarrierPrintShipmentStart', 'CarrierPrintShipmentResult',
    'CarrierPrintShipment', 'CarrierGetLabelStart', 'CarrierGetLabelResult',
    'CarrierGetLabel']
__metaclass__ = PoolMeta

_SHIPMENT_STATES = ['packed', 'done']
logger = logging.getLogger(__name__)


class Configuration:
    __name__ = 'stock.configuration'
    attach_label = fields.Boolean('Attach Label')


class ShipmentOut:
    __name__ = 'stock.shipment.out'
    carrier_service_domain = fields.Function(fields.One2Many(
            'carrier.api.service', None, 'Carrier Domain',
            depends=['carrier']),
        'on_change_with_carrier_service_domain')
    carrier_service = fields.Many2One('carrier.api.service',
        'Carrier API Service',
        domain=[
            ('id', 'in', Eval('carrier_service_domain')),
            ],
        states={
            'invisible': ~Eval('carrier'),
            }, depends=['carrier', 'state', 'carrier_service_domain'])
    carrier_delivery = fields.Boolean('Delivered', readonly=True,
        states={
            'invisible': ~Eval('carrier'),
            }, help='The package has been delivered')
    carrier_printed = fields.Boolean('Printed', readonly=True,
        states={
            'invisible': ~Eval('carrier'),
            }, help='Picking is already printed')
    carrier_notes = fields.Char('Carrier Notes', help='Notes to add carrier')
    carrier_send_employee = fields.Many2One('company.employee', 'Carrier Send Employee', readonly=True)
    carrier_send_date = fields.DateTime('Carrier Send Date', readonly=True)

    @classmethod
    def __setup__(cls):
        super(ShipmentOut, cls).__setup__()
        if 'carrier' not in cls.carrier_cashondelivery_total.depends:
            cls.carrier_cashondelivery_total.depends.append('carrier')
        if ('cost_currency_digits' not in
                cls.carrier_cashondelivery_total.depends):
            cls.carrier_cashondelivery_total.depends.append(
                'cost_currency_digits')
        cls._error_messages.update({
            'not_carrier': 'Shipment "%(name)s" not have carrier',
            'not_carrier_api': 'Carrier "%(name)s" not have API',
            'shipmnet_delivery_address': 'Shipment "%(name)s" not have address details: '
                'street, zip, city or country.',
            })
        cls._buttons.update({
                'wizard_carrier_send_shipments': {
                    'invisible': (~Eval('state').in_(_SHIPMENT_STATES)) |
                        (Eval('carrier_delivery')) |
                        Not(Bool(Eval('carrier'))),
                    },
                'wizard_carrier_print_shipment': {
                    'invisible': (~Eval('state').in_(_SHIPMENT_STATES)) |
                        (Eval('carrier_printed')) | Not(Bool(Eval('carrier'))),
                    },
                })

    def _comment2txt(self, comment):
        return comment.replace('\n', '. ').replace('\r', '')

    def on_change_with_carrier_service_domain(self, name=None):
        ApiCarrier = Pool().get('carrier.api-carrier.carrier')
        carrier_api_services = []
        if self.carrier:
            api_carriers = ApiCarrier.search([
                    ('carrier', '=', self.carrier.id)])
            carrier_api_services = [service.id for api_carrier in api_carriers
                for service in api_carrier.api.services]
        return carrier_api_services

    def on_change_customer(self):
        super(ShipmentOut, self).on_change_customer()

        carrier_notes = None
        if self.customer:
            address = self.customer.address_get(type='delivery')
            if address.comment_shipment:
                carrier_notes = self._comment2txt(address.comment_shipment)
            elif self.customer.comment_shipment:
                carrier_notes = self._comment2txt(self.customer.comment_shipment)
        self.carrier_notes = carrier_notes

    def on_change_carrier(self):
        super(ShipmentOut, self).on_change_carrier()
        self.carrier_service = None

    @classmethod
    @ModelView.button_action('carrier_send_shipments.'
        'wizard_carrier_send_shipments')
    def wizard_carrier_send_shipments(cls, shipments):
        pass

    @classmethod
    @ModelView.button_action('carrier_send_shipments.'
        'wizard_carrier_print_shipment')
    def wizard_carrier_print_shipment(cls, shipments):
        pass

    @classmethod
    def copy(cls, shipments, default=None):
        if default is None:
            default = {}
        default = default.copy()
        default['carrier_delivery'] = None
        default['carrier_printed'] = None
        return super(ShipmentOut, cls).copy(shipments, default=default)

    @staticmethod
    def get_price_ondelivery_shipment_out(shipment):
        '''Get price ondelivery from shipment out'''
        if shipment.carrier_cashondelivery_total:
            price_ondelivery = shipment.carrier_cashondelivery_total
        elif shipment.carrier_sale_price_total:
            price_ondelivery = shipment.carrier_sale_price_total
        else:
            price_ondelivery = shipment.total_amount
        return price_ondelivery

    @staticmethod
    def get_phone_shipment_out(shipment):
        '''Get default phone from shipment out'''
        if shipment.delivery_address.mobile:
            return shipment.delivery_address.mobile
        if shipment.delivery_address.phone:
            return shipment.delivery_address.phone
        return shipment.company.party.phone if shipment.company.party.phone else ''

    @staticmethod
    def get_carrier_employee():
        User = Pool().get('res.user')
        if Transaction().context.get('employee'):
            return Transaction().context['employee']
        else:
            user = User(Transaction().user)
            if user.employee:
                return user.employee.id

    @staticmethod
    def get_carrier_date():
        return datetime.now()

    @classmethod
    def send_shipment_api(cls, shipment):
        '''Send Shipmemt to carrier API'''
        pool = Pool()
        Shipment = pool.get('stock.shipment.out')
        API = pool.get('carrier.api')
        Config = pool.get('stock.configuration')
        Attachment = pool.get('ir.attachment')

        config_stock = Config(1)
        attach_label = config_stock.attach_label

        if not shipment.carrier:
            message = cls.raise_user_error('not_carrier', {
                    'name': shipment.rec_name,
                    }, raise_exception=False)
            refs = []
            labs = []
            errs = [message]
            return refs, labs, errs

        apis = API.search([('carriers', 'in', [shipment.carrier.id])],
            limit=1)
        if not apis:
            message = cls.raise_user_error('not_carrier_api', {
                    'name': shipment.carrier.rec_name,
                    }, raise_exception=False)
            logger.warning(message)
            refs = []
            labs = []
            errs = [message]
            return refs, labs, errs

        api, = apis

        if not shipment.delivery_address.street or not shipment.delivery_address.zip \
                or not shipment.delivery_address.city or not shipment.delivery_address.country:
            message = cls.raise_user_error('shipmnet_delivery_address', {
                        'name': shipment.rec_name,
                        }, raise_exception=False)
            logger.warning(message)
            refs = []
            labs = []
            errs = [message]
        else:
            send_shipment = getattr(Shipment, 'send_%s' % api.method)
            refs, labs, errs = send_shipment(api, [shipment])

            if attach_label and labs:
                attach = Attachment(
                    name=datetime.now().strftime("%y/%m/%d %H:%M:%S"),
                    type='data',
                    data=fields.Binary.cast(open(labs[0], "rb").read()),
                    resource=str(shipment))
                attach.save()
        return refs, labs, errs


class CarrierSendShipmentsStart(ModelView):
    'Carrier Send Shipments Start'
    __name__ = 'carrier.send.shipments.start'
    shipments = fields.Many2Many('stock.shipment.out', None, None,
        'Shipments', readonly=True)

    @staticmethod
    def default_shipments():
        return Transaction().context['active_ids']


class CarrierSendShipmentsResult(ModelView):
    'Carrier Send Shipments Result'
    __name__ = 'carrier.send.shipments.result'
    info = fields.Text('Info', readonly=True)
    labels = fields.Binary('Labels', filename='file_name')
    file_name = fields.Text('File Name')


class CarrierSendShipments(Wizard):
    'Carrier Send Shipments'
    __name__ = "carrier.send.shipments"
    start = StateView('carrier.send.shipments.start',
        'carrier_send_shipments.carrier_send_shipments_start', [
            Button('Cancel', 'end', 'tryton-cancel'),
            Button('Send', 'send', 'tryton-ok', default=True),
            ])
    send = StateTransition()
    result = StateView('carrier.send.shipments.result',
        'carrier_send_shipments.carrier_send_shipments_result', [
            Button('Close', 'end', 'tryton-close'),
            ])
    print_ = StateAction(
        'carrier_send_shipments.wizard_carrier_print_shipment')

    @classmethod
    def __setup__(cls):
        super(CarrierSendShipments, cls).__setup__()
        cls._error_messages.update({
            'shipment_state': 'Shipment ID (%(shipment)s) not state '
                '"%(state)s"',
            'shipment_sended': 'Shipment (%(shipment)s) was sended',
            'add_carrier': 'Select a carrier in shipment "%(shipment)s"',
            'carrier_api': 'Not available method API in carrier "%(carrier)s"',
            'shipment_info':
                'Successfully:\n%(references)s\n\nErrors:\n%(errors)s',
            'shipment_different_carrier': 'You select different shipments to '
                'send %(methods)s. Select shipment grouped by carrier',
            'shipment_zip': 'Shipment "%(code)s" not available to send zip '
                '"%(zip)s"',
            })

    def transition_send(self):
        pool = Pool()
        Shipment = pool.get('stock.shipment.out')

        dbname = Transaction().cursor.dbname
        references = []
        labels = []
        errors = []

        shipments = Shipment.search([
                ('id', 'in', Transaction().context['active_ids']),
                ])
        for shipment in shipments:
            refs, labs, errs = Shipment.send_shipment_api(shipment)

            references += refs
            labels += labs
            errors += errs

        #  Save results in info and labels fields
        self.result.info = self.raise_user_error('shipment_info', {
                'references': ', '.join(references) if references else '',
                'errors': ', '.join(errors) if errors else '',
                }, raise_exception=False)

        #  Save file label in labels field
        if len(labels) == 1:  # A label generate simple file
            label, = labels
            carrier_labels = fields.Binary.cast(open(label, "rb").read())
            file_name = label.split('/')[2]
        elif len(labels) > 1:  # Multiple labels generate tgz
            temp = tempfile.NamedTemporaryFile(prefix='%s-carrier-' % dbname,
                delete=False)
            temp.close()
            with tarfile.open(temp.name, "w:gz") as tar:
                for path_label in labels:
                    tar.add(path_label)
            tar.close()
            carrier_labels = fields.Binary.cast(open(temp.name, "rb").read())
            file_name = '%s.tgz' % temp.name.split('/')[2]
        else:
            carrier_labels = None
            file_name = None
        self.result.labels = carrier_labels
        self.result.file_name = file_name

        return 'result'

    def default_start(self, fields):
        Shipment = Pool().get('stock.shipment.out')
        API = Pool().get('carrier.api')

        methods = []
        default = {}

        shipments = Shipment.search([
                ('id', 'in', Transaction().context['active_ids']),
                ])
        for shipment in shipments:
            if not shipment.state in _SHIPMENT_STATES:
                self.raise_user_error('shipment_state', {
                        'shipment': shipment.id,
                        'state': ', '.join(_SHIPMENT_STATES)
                        })
            if not shipment.carrier:
                self.raise_user_error('add_carrier', {
                        'shipment': shipment.code,
                        })
            if shipment.carrier_tracking_ref:
                self.raise_user_error('shipment_sended', {
                        'shipment': shipment.code,
                        })
            carrier = shipment.carrier.rec_name
            apis = API.search([('carriers', 'in', [shipment.carrier.id])],
                limit=1)
            if not apis:
                self.raise_user_error('carrier_api', {
                        'carrier': carrier,
                        })
            api = apis[0]

            if api.zips:
                zips = api.zips.split(',')
                if (shipment.delivery_address.zip
                        and shipment.delivery_address.zip in zips):
                    self.raise_user_error('shipment_zip', {
                            'code': shipment.code,
                            'zip': shipment.delivery_address.zip,
                            })

            if not api.method in methods:
                methods.append(api.method)

        if len(methods) > 1:
            self.raise_user_error('shipment_different_carrier', {
                    'methods': ', '.join(methods),
                    })

        default['carrier'] = shipment.carrier.id
        if api.default_service:
            default['service'] = api.default_service.id
        return default

    def default_result(self, fields):
        return {
            'info': self.result.info,
            'labels': self.result.labels,
            'file_name': self.result.file_name,
            }

    def do_print_(self, action):
        active_ids = Transaction().context['active_ids']
        return action, {'ids': active_ids}


class CarrierPrintShipmentStart(ModelView):
    'Carrier Print Shipment Start'
    __name__ = 'carrier.print.shipment.start'
    shipments = fields.Many2Many('stock.shipment.out', None, None,
        'Shipments', readonly=True)

    @staticmethod
    def default_shipments():
        return Transaction().context['active_ids']


class CarrierPrintShipmentResult(ModelView):
    'Carrier Print Shipment Result'
    __name__ = 'carrier.print.shipment.result'
    labels = fields.Binary('Labels', filename='file_name')
    file_name = fields.Char('File Name')


class CarrierPrintShipment(Wizard):
    'Carrier Print Shipment'
    __name__ = "carrier.print.shipment"
    start = StateView('carrier.print.shipment.start',
        'carrier_send_shipments.carrier_print_shipment_start', [
            Button('Cancel', 'end', 'tryton-cancel'),
            Button('Print', 'print_', 'tryton-ok', default=True),
            ])
    print_ = StateTransition()
    result = StateView('carrier.print.shipment.result',
        'carrier_send_shipments.carrier_print_shipment_result', [
            Button('Close', 'end', 'tryton-close'),
            ])

    @classmethod
    def __setup__(cls):
        super(CarrierPrintShipment, cls).__setup__()
        cls._error_messages.update({
            'shipment_state_mismatch': 'The shipment %(shipment)s is not in '
                'any of these states "%(state)s".',
            'no_carrier_assigned': 'The shipment "%(shipment)s" has not any '
                'carrier assigned. Please, select one carrier before trying '
                'to print the label.',
            'shipment_already_sent': 'The shipment (%(shipment)s) is already '
                'sent.',
            'carrier_without_api': 'The carrier "%(carrier)s" has not any '
                'API method available.',
            'shipment_zip_unavailable': 'The zip "%(zip)s" of the shipment '
                '"%(shipment)s" is not available for this carrier.',
            'method_mismatch': 'You\'ve selected shipments with different '
                'methods of shipping. Please, select shipments of a unique '
                'carrier.',
        })

    def default_start(self, fields):
        pool = Pool()
        Shipment = pool.get('stock.shipment.out')
        API = pool.get('carrier.api')

        methods = set()
        default = {}
        shipments = Shipment.search([
                ('id', 'in', Transaction().context['active_ids']),
                ])

        for shipment in shipments:
            if not shipment.state in _SHIPMENT_STATES:
                self.raise_user_error('shipment_state_mismatch', {
                        'shipment': shipment.code,
                        'state': ', '.join(_SHIPMENT_STATES)
                        })

            carrier = shipment.carrier
            if not carrier:
                self.raise_user_error('no_carrier_assigned', {
                        'shipment': shipment.code,
                        })

            apis = API.search([('carriers', 'in', [carrier.id])], limit=1)
            if not apis:
                self.raise_user_error('carrier_without_api', {
                        'carrier': carrier.rec_name,
                        })

            api = apis[0]
            if api.zips:
                zips = [z.strip() for z in api.zips.split(',')]
                zip_code = (shipment.delivery_address
                    and shipment.delivery_address.zip or 0)
                if zip_code and zip_code in zips:
                    self.raise_user_error('shipment_zip_unavailable', {
                            'shipment': shipment.code,
                            'zip': zip_code,
                            })

            if shipment.carrier_delivery:
                default['carrier_printed'] = True

            methods.add(api.method)
            if len(methods) > 1:
                self.raise_user_error('method_mismatch')

        default['carrier'] = carrier.id
        default['printed'] = bool([s for s in shipments if s.carrier_printed])

        return default

    def default_result(self, fields):
        return {
            'labels': self.result.labels,
            'file_name': self.result.file_name,
            }

    def transition_print_(self):
        pool = Pool()
        Shipment = pool.get('stock.shipment.out')
        API = pool.get('carrier.api')
        Attachment = pool.get('ir.attachment')
        Config = pool.get('stock.configuration')

        config_stock = Config(1)
        attach_label = config_stock.attach_label

        dbname = Transaction().cursor.dbname
        labels = []

        shipments = Shipment.search([
                ('id', 'in', Transaction().context['active_ids']),
                ])
        for shipment in shipments:
            apis = API.search([('carriers', 'in', [shipment.carrier.id])],
                limit=1)
            if not apis:
                continue
            api, = apis

            print_label = getattr(Shipment, 'print_labels_%s' % api.method)
            labs = print_label(api, [shipment])

            if attach_label and labs:
                attach = Attachment(
                    name=datetime.now().strftime("%y/%m/%d %H:%M:%S"),
                    type='data',
                    data=fields.Binary.cast(open(labs[0], "rb").read()),
                    resource=str(shipment))
                attach.save()

            labels += labs

        #  Save file label in labels field
        if len(labels) == 1:  # A label generate simple file
            label, = labels
            carrier_labels = fields.Binary.cast(open(label, "rb").read())
            file_name = label.split('/')[2]
        elif len(labels) > 1:  # Multiple labels generate tgz
            temp = tempfile.NamedTemporaryFile(prefix='%s-carrier-' % dbname,
                delete=False)
            temp.close()
            with tarfile.open(temp.name, "w:gz") as tar:
                for path_label in labels:
                    tar.add(path_label)
            tar.close()
            carrier_labels = fields.Binary.cast(open(temp.name, "rb").read())
            file_name = '%s.tgz' % temp.name.split('/')[2]
        else:
            carrier_labels = None
            file_name = None
        self.result.labels = carrier_labels
        self.result.file_name = file_name

        return 'result'


class CarrierGetLabelStart(ModelView):
    'Carrier Get Label Start'
    __name__ = 'carrier.get.label.start'
    codes = fields.Char('Codes', required=True,
        help='Introduce codes or tracking reference of shipments separated by commas.')


class CarrierGetLabelResult(ModelView):
    'Carrier Get Label Result'
    __name__ = 'carrier.get.label.result'
    attachments = fields.One2Many('ir.attachment', None, 'Attachments',
        states={
            'invisible': Not(Bool(Eval('attachments'))),
            'readonly': True,
            })


class CarrierGetLabel(Wizard):
    'Carrier Get Label'
    __name__ = "carrier.get.label"
    start = StateView('carrier.get.label.start',
        'carrier_send_shipments.carrier_get_label_start_view_form', [
            Button('Cancel', 'end', 'tryton-cancel'),
            Button('Get', 'get', 'tryton-ok', default=True),
            ])
    get = StateTransition()
    result = StateView('carrier.get.label.result',
        'carrier_send_shipments.carrier_get_label_result_view_form', [
            Button('Close', 'end', 'tryton-close'),
            ])

    def transition_get(self):
        pool = Pool()
        Attachment = pool.get('ir.attachment')
        Shipment = pool.get('stock.shipment.out')
        API = pool.get('carrier.api')

        codes = [l.strip() for l in self.start.codes.split(',')]
        shipments = Shipment.search([
                ('state', 'in', _SHIPMENT_STATES),
                ['OR',
                    ('code', 'in', codes),
                    ('carrier_tracking_ref', 'in', codes),
                ]])

        if not shipments:
            return 'result'

        apis = {}
        for shipment in shipments:
            if not shipment.carrier:
                continue
            carrier_apis = API.search([('carriers', 'in', [shipment.carrier.id])],
                limit=1)
            if not carrier_apis:
                continue
            api, = carrier_apis

            if apis.get(api.method):
                shipments = apis.get(api.method)
                shipments.append(shipment)
            else:
                shipments = [shipment]
            apis[api.method] = shipments

        attachments = []
        for method, shipments in apis.iteritems():
            api, = API.search([('method', '=', method)],
                limit=1)
            print_label = getattr(Shipment, 'print_labels_%s' % method)
            labels = print_label(api, shipments)

            for label, shipment in zip(labels, shipments):
                attach = {
                    'name': datetime.now().strftime("%y/%m/%d %H:%M:%S"),
                    'type': 'data',
                    'data': fields.Binary.cast(open(label, "rb").read()),
                    'description': '%s - %s' % (shipment.code, method),
                    'resource': '%s' % str(shipment),
                    }

                attachments.append(attach)
                
        attachments = Attachment.create(attachments)
        self.result.attachments = attachments

        return 'result'

    def default_result(self, fields):
        return {
            'attachments': [a.id
                for a in getattr(self.result, 'attachments', [])],
            }

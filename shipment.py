# This file is part of the carrier_send_shipments module for Tryton.
# The COPYRIGHT file at the top level of this repository contains
# the full copyright notices and license terms.
from trytond.model import ModelView, fields
from trytond.wizard import Wizard, StateTransition, StateView, Button
from trytond.pool import Pool, PoolMeta
from trytond.transaction import Transaction
from trytond.pyson import Eval

__all__ = ['ShipmentOut', 'CarrierSendShipmentsStart',
        'CarrierSendShipmentsResult', 'CarrierSendShipments',
        'CarrierPrintShipmentStart', 'CarrierPrintShipmentResult',
        'CarrierPrintShipment']
__metaclass__ = PoolMeta
_SHIPMENT_STATES = ['packed', 'done']


class ShipmentOut:
    "Customer Shipment"
    __name__ = 'stock.shipment.out'
    cash_ondelivery = fields.Numeric('Cash OnDelivery',
            digits=(16, Eval('cost_currency_digits', 2)), states={
            'invisible': ~Eval('carrier'),
            'readonly': ~Eval('state').in_(['draft', 'waiting', 'assigned',
                    'packed']),
            }, depends=['carrier', 'state', 'cost_currency_digits'])
    carrier_service = fields.Many2One('carrier.service', 'Carrier service',
            states={
                'invisible': ~Eval('carrier'),
            },
            readonly=True,
            depends=['carrier', 'state'],
            domain=[('carrier', '=', Eval('carrier'))])
    carrier_delivery = fields.Boolean('Delivered', readonly=True,
            states={
                'invisible': ~Eval('carrier'),
            }, help='The package has been delivered')
    carrier_printed = fields.Boolean('Printed', help='Picking is already printed',
        readonly=True)


class CarrierSendShipmentsStart(ModelView):
    'Carrier Send Shipments Start'
    __name__ = 'carrier.send.shipments.start'
    carrier = fields.Many2One('carrier', 'Carrier', required=True,
        readonly=True)
    service = fields.Many2One('carrier.service', 'Service', required=True,
            depends=['carrier'], domain=[('carrier', '=', Eval('carrier'))])


class CarrierSendShipmentsResult(ModelView):
    'Carrier Send Shipments Result'
    __name__ = 'carrier.send.shipments.result'
    info = fields.Text('Info', readonly=True)


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

    @classmethod
    def __setup__(cls):
        super(CarrierSendShipments, cls).__setup__()
        cls._error_messages.update({
            'shipment_state': 'Shipment ID (%(shipment)s) not state '
                '"%(state)s"',
            'shipment_sended': 'Shipment (%(shipment)s) was sended',
            'add_carrier': 'Select a carrier in shipment "%(shipment)s"',
            'carrier_api': 'Not available method API in carrier "%(carrier)s"',
            'send_shipment_info': 'Send shipments:\nCodes: %(codes)s\n' \
                'Carrier: "%(carrier)s"\nService "%(service)s"',
            'shipment_different_carrier': 'You select different shipments to '
                'send %(methods)s. Select shipment grouped by carrier',
            'shipment_zip': 'Shipment "%(code)s" not available to send zip '
                '"%(zip)s"',
        })

    def transition_send(self):
        Shipment = Pool().get('stock.shipment.out')
        API = Pool().get('carrier.api')

        carrier = self.start.carrier
        service = self.start.service

        apis = API.search([('carrier', '=', carrier)], limit=1)
        if not apis:
            self.raise_user_error('carrier_api', {
                    'carrier': carrier,
                    })
        api = apis[0]
        method = apis[0].method

        shipments = Shipment.search([
                ('id', 'in', Transaction().context['active_ids']),
                ])
        send_shipment = getattr(Shipment, 'send_%s' % method)
        send_shipment(api, shipments, service)

        codes = []
        for shipment in shipments:
            codes.append(shipment.code)

        self.result.info = self.raise_user_error('send_shipment_info', {
                    'codes': ', '.join(codes),
                    'carrier': carrier.rec_name,
                    'service': service.name,
                    }, raise_exception=False)
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
            if shipment.carrier_delivery:
                self.raise_user_error('shipment_sended', {
                        'shipment': shipment.code,
                        })
            carrier = shipment.carrier.rec_name
            apis = API.search([('carrier', '=', shipment.carrier)], limit=1)
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
        if api.service:
            default['service'] = api.service.id
        return default

    def default_result(self, fields):
        return {
            'info': self.result.info,
            }


class CarrierPrintShipmentStart(ModelView):
    'Carrier Print Shipment Start'
    __name__ = 'carrier.print.shipment.start'
    carrier = fields.Many2One('carrier', 'Carrier', required=True,
        readonly=True)
    printed = fields.Boolean('Printed', help='Picking is already printed.',
        readonly=True)


class CarrierPrintShipmentResult(ModelView):
    'Carrier Print Shipment Result'
    __name__ = 'carrier.print.shipment.result'
    archive = fields.Binary('Archive')
    name = fields.Char('Archive Name')


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
        Shipment = Pool().get('stock.shipment.out')
        API = Pool().get('carrier.api')

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

            apis = API.search([('carrier', '=', carrier)], limit=1)
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
        default['printed'] = bool([s for s in shipments if s.printed])

        return default

    def transition_print_(self):
        Shipment = Pool().get('stock.shipment.out')
        API = Pool().get('carrier.api')

        carrier = self.start.carrier
        api, = API.search([('carrier', '=', carrier)], limit=1)
        method = api.method

        print_label = getattr(Shipment, 'print_labels_%s' % method)
        shipments = Shipment.search([
                ('id', 'in', Transaction().context['active_ids']),
                ])
        labels = print_label(shipments, api)

        self.result.archive = ''.join(labels)
        self.result.name = 'carrier.pdf'

        return 'result'

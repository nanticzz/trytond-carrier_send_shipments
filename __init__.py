#This file is part carrier_send_shipments module for Tryton.
#The COPYRIGHT file at the top level of this repository contains
#the full copyright notices and license terms.
from trytond.pool import Pool
from .shipment import *

def register():
    Pool.register(
        ShipmentOut,
        CarrierSendShipmentsStart,
        CarrierSendShipmentsResult,
        module='carrier_send_shipments', type_='model')
    Pool.register(
        CarrierSendShipments,
        module='carrier_send_shipments', type_='wizard')


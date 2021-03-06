import json

from ledger.util import F
from stp_core.loop.eventually import eventually
from plenum.common.exceptions import NoConsensusYet, OperationError
from stp_core.common.log import getlogger
from plenum.common.constants import TARGET_NYM, TXN_TYPE, DATA, NAME, \
    VERSION, TYPE, ORIGIN

from sovrin_common.constants import GET_SCHEMA, SCHEMA, ATTR_NAMES, \
    GET_ISSUER_KEY, REF, ISSUER_KEY, PRIMARY, REVOCATION

from anoncreds.protocol.repo.public_repo import PublicRepo
from anoncreds.protocol.types import Schema, ID, PublicKey, \
    RevocationPublicKey, AccumulatorPublicKey, \
    Accumulator, TailsType, TimestampType
from sovrin_common.types import Request


def _ensureReqCompleted(reqKey, client, clbk):
    reply, err = client.replyIfConsensus(*reqKey)

    if err:
        raise OperationError(err)

    if reply is None:
        raise NoConsensusYet('not completed')

    return clbk(reply, err)


def _getData(result, error):
    data = json.loads(result.get(DATA).replace("\'", '"'))
    seqNo = None if not data else data.get(F.seqNo.name)
    return data, seqNo


def _submitData(result, error):
    data = json.loads(result.get(DATA).replace("\'", '"'))
    seqNo = result.get(F.seqNo.name)
    return data, seqNo


logger = getlogger()


class SovrinPublicRepo(PublicRepo):
    def __init__(self, client, wallet):
        self.client = client
        self.wallet = wallet
        self.displayer = print

    async def getSchema(self, id: ID) -> Schema:
        op = {
            TARGET_NYM: id.schemaKey.issuerId,
            TXN_TYPE: GET_SCHEMA,
            DATA: {
                NAME: id.schemaKey.name,
                VERSION: id.schemaKey.version,
            }
        }
        data, seqNo = await self._sendGetReq(op)
        return Schema(name=data[NAME],
                               version=data[VERSION],
                               schemaType=data[TYPE],
                               attrNames=data[ATTR_NAMES].split(","),
                               issuerId=data[ORIGIN],
                               seqId=seqNo)

    async def getPublicKey(self, id: ID) -> PublicKey:
        op = {
            TXN_TYPE: GET_ISSUER_KEY,
            REF: id.schemaId,
            ORIGIN: id.schemaKey.issuerId
        }

        data, seqNo = await self._sendGetReq(op)

        data = data[DATA][PRIMARY]
        pk = PublicKey.fromStrDict(data)._replace(seqId=seqNo)
        return pk

    async def getPublicKeyRevocation(self, id: ID) -> RevocationPublicKey:
        op = {
            TXN_TYPE: GET_ISSUER_KEY,
            REF: id.schemaId,
            ORIGIN: id.schemaKey.issuerId
        }

        data, seqNo = await self._sendGetReq(op)

        if not data:
            return None

        data = data[DATA][REVOCATION]
        pkR = RevocationPublicKey.fromStrDict(data)._replace(seqId=seqNo)
        return pkR

    async def getPublicKeyAccumulator(self, id: ID) -> AccumulatorPublicKey:
        pass

    async def getAccumulator(self, id: ID) -> Accumulator:
        pass

    async def getTails(self, id: ID) -> TailsType:
        pass

    # SUBMIT

    async def submitSchema(self,
                           schema: Schema) -> Schema:
        op = {
            TXN_TYPE: SCHEMA,
            DATA: {
                NAME: schema.name,
                VERSION: schema.version,
                TYPE: schema.schemaType,
                ATTR_NAMES: ",".join(schema.attrNames)
            }
        }

        data, seqNo = await self._sendSubmitReq(op)

        if not seqNo:
            return None
        schema = schema._replace(issuerId=self.wallet.defaultId,
                                 seqId=seqNo)
        return schema

    async def submitPublicKeys(self, id: ID, pk: PublicKey,
                               pkR: RevocationPublicKey = None) -> (
            PublicKey, RevocationPublicKey):
        pkData = pk.toStrDict()
        pkRData = pkR.toStrDict()
        op = {
            TXN_TYPE: ISSUER_KEY,
            REF: id.schemaId,
            DATA: {PRIMARY: pkData, REVOCATION: pkRData}
        }

        data, seqNo = await self._sendSubmitReq(op)

        if not seqNo:
            return None
        pk = pk._replace(seqId=seqNo)
        pkR = pkR._replace(seqId=seqNo)
        return pk, pkR

    async def submitAccumulator(self, id: ID, accumPK: AccumulatorPublicKey,
                                accum: Accumulator, tails: TailsType):
        pass

    async def submitAccumUpdate(self, id: ID, accum: Accumulator,
                                timestampMs: TimestampType):
        pass

    async def _sendSubmitReq(self, op):
        return await self._sendReq(op, _submitData)

    async def _sendGetReq(self, op):
        return await self._sendReq(op, _getData)

    async def _sendReq(self, op, clbk):
        req = Request(identifier=self.wallet.defaultId, operation=op)
        req = self.wallet.prepReq(req)
        self.client.submitReqs(req)
        try:
            # TODO: Come up with an explanation, why retryWait had to be
            # increases to 1 from .5 to pass some tests and from 1 to 2 to
            # pass some other tests. The client was not getting a chance to
            # service its stack, we need to find a way to stop this starvation.
            resp = await eventually(_ensureReqCompleted,
                                    req.key, self.client, clbk,
                                    timeout=20, retryWait=2)
        except NoConsensusYet:
            raise TimeoutError('Request timed out')
        return resp

# -*- coding: utf-8 -*-
# Copyright 2015 OpenMarket Ltd
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.


from twisted.internet import defer

from synapse.events.utils import prune_event, old_prune_event

from syutil.jsonutil import encode_canonical_json

from synapse.crypto.event_signing import check_event_content_hash

from synapse.api.errors import SynapseError

import logging


logger = logging.getLogger(__name__)


class FederationBase(object):
    @defer.inlineCallbacks
    def _check_sigs_and_hash_and_fetch(self, origin, pdus, outlier=False):
        """Takes a list of PDUs and checks the signatures and hashs of each
        one. If a PDU fails its signature check then we check if we have it in
        the database and if not then request if from the originating server of
        that PDU.

        If a PDU fails its content hash check then it is redacted.

        The given list of PDUs are not modified, instead the function returns
        a new list.

        Args:
            pdu (list)
            outlier (bool)

        Returns:
            Deferred : A list of PDUs that have valid signatures and hashes.
        """
        signed_pdus = []
        for pdu in pdus:
            try:
                new_pdu = yield self._check_sigs_and_hash(pdu)
                signed_pdus.append(new_pdu)
            except SynapseError:
                # FIXME: We should handle signature failures more gracefully.

                # Check local db.
                new_pdu = yield self.store.get_event(
                    pdu.event_id,
                    allow_rejected=True
                )
                if new_pdu:
                    signed_pdus.append(new_pdu)
                    continue

                # Check pdu.origin
                if pdu.origin != origin:
                    new_pdu = yield self.get_pdu(
                        destinations=[pdu.origin],
                        event_id=pdu.event_id,
                        outlier=outlier,
                    )

                    if new_pdu:
                        signed_pdus.append(new_pdu)
                        continue

                logger.warn("Failed to find copy of %s with valid signature")

        defer.returnValue(signed_pdus)

    @defer.inlineCallbacks
    def _check_sigs_and_hash(self, pdu):
        """Throws a SynapseError if the PDU does not have the correct
        signatures.

        Returns:
            FrozenEvent: Either the given event or it redacted if it failed the
            content hash check.
        """
        # Check signatures are correct.
        redacted_event = prune_event(pdu)
        redacted_pdu_json = redacted_event.get_pdu_json()

        old_redacted = old_prune_event(pdu)
        old_redacted_pdu_json = old_redacted.get_pdu_json()

        try:
            try:
                yield self.keyring.verify_json_for_server(
                    pdu.origin, old_redacted_pdu_json
                )
            except SynapseError:
                yield self.keyring.verify_json_for_server(
                    pdu.origin, redacted_pdu_json
                )
        except SynapseError:
            logger.warn(
                "Signature check failed for %s redacted to %s",
                encode_canonical_json(pdu.get_pdu_json()),
                encode_canonical_json(redacted_pdu_json),
            )
            raise

        if not check_event_content_hash(pdu):
            logger.warn(
                "Event content has been tampered, redacting %s, %s",
                pdu.event_id, encode_canonical_json(pdu.get_dict())
            )
            defer.returnValue(redacted_event)

        defer.returnValue(pdu)
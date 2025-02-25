import os
import requests
import json
from typing import Any, List, Optional
from dataclasses import dataclass, field

from eve_tools.config import METADATA_PATH

from .param import ESIParams, Param
from .token import Token


@dataclass
class ESIRequest:
    """Holds information of a request to ESI.

    Each ESIRequest is generated by the getitem method in ESIMetadata class,
    with request_key, request_type, parameters, and security set.
    The ESI class uses the ESIRequest got from ESIMetadata to perform parameter parse and check,
    and fill in the url, headers, params field.

    Args:
        request_key: str
            A string for the request path, such as "/characters/{character_id}/search/"
        request_type: str
            A string for the request type, such as "get" (not "GET").
        parameters: ESIParams
            A lists of Params hold by ESIParams.
        security: List[str]
            A list of str representing authentication scopes.
            API request with <= 1 scopes is supported.
        url: Optional[str]
            A string to request to. Contains the base url.
        headers: Optional[dict]
            A dictionary containing request headers,
            usually contains "Authorization" field, or other user defined fields.
        params: Optional[dict]
            A dictionary containing params for the url.
            Contains necessary info for ESI, such as {"type_id": 12005}
        kwd: Optional[dict]
            A dictionary containing keywords used for the request.
        token: Optional[Token]
            A Token instance used for this request. Default None if request is unauthenticated.
    """

    request_key: Optional[str] = None  # paths[key]
    request_type: Optional[str] = None  # 'GET' or 'POST'

    parameters: Optional[ESIParams] = None
    security: List[str] = None  # default to []

    url: Optional[str] = None
    headers: Optional[dict] = field(default_factory=dict)
    params: Optional[dict] = field(default_factory=dict)
    kwd: Optional[dict] = field(default_factory=dict)

    token: Optional[Token] = None


class ESIMetadata(object):
    """Holds and parse metadata from EVE ESI.

    Retrieve metadata from either ESI or local file.
    Parses metadata into ESIRequest for a given key in getitem method.

    EVE ESI provides metadata for swagger clients.
    Since this package does not use pyswagger, metadata parsing is necessary.

    Does not support setitem on ESIMetadata.
    """

    def __init__(self):
        self.paths = None
        self.securityDefinitions = None

        self._metaParams = None  # params that exist in metadata["parameters"]
        self._load_metadata()

    def __getitem__(self, key: str) -> ESIRequest:
        """Get an ESIRequest with key.

        Parse and find metadata entry with the given key.
        Assume each metadata entry is ONE of GET, POST.
        Some ESI API has DELETE or PUT, such as /characters/{character_id}/contacts/,
        but they tend to be trivial in market data analysis, so they are not supported.

        Returns:
            An ESIRequest instance with request_key = key.

        Raises:
            KeyError: key is not a valid request type.
        """
        if not key in self.paths.keys():
            raise KeyError(f"{key} is not a valid request type.")

        request_key = key
        request_type = list(self.paths[key].keys())
        if len(request_type) > 1:
            # log warning
            # find "get" or "post" method
            for t in request_type:
                if t == "get" or t == "post":
                    request_type = t

        request_type = request_type[0]
        request_body = self.paths[key][request_type]
        parameters = self._parse_parameters(request_body)
        security = self._parse_security(request_body)

        return ESIRequest(request_key, request_type, parameters, security)

    def __setitem__(self, key: Any, value: Any):
        raise TypeError("ESIMetadata is not writable")

    def _parse_parameters(self, body: dict) -> ESIParams:
        """Parse parameters of the metadata for a request.

        Every ESI API has a "parameters" field. Parse parameters into ESIParams.

        Returns:
            An ESIParams instance with all Param(s) instanciated.
        """
        parameters = body["parameters"]
        params = []
        for param in parameters:
            # check for {$ref : #/parameters/xxx} type param
            # Parameters defined in metadata["parameters"] has key pattern: "$ref/parameters/xxx".
            # Ignore parameters with $ref/parameters signature but not in metadata["parameters"] field.
            metaparam = param.get("$ref", "")  # $ref for meta parameters
            if metaparam:
                param_ = self._metaParams[metaparam.split("/")[-1]]
                if param_:
                    params.append(param_)
                continue

            # construct Param class
            # Param.default is only present in meta parameters.
            params.append(
                Param(
                    param["name"],
                    param["in"],
                    param.get("required", False),
                    param.get("type", ""),
                )
            )

        return ESIParams(params)

    def _parse_security(self, body: dict) -> List[str]:
        """Parse security entry of the metadata for a request.

        Each API request should have either zero or one type of security, aka "evesso".
        Also assume each API request has exactly ONE scope.
        API request with multiple scopes will yield error in finding token in ESITokens.

        Raises:
            ValueError: API request with multiple scopes is not supported.
        """
        security = body.get("security", None)
        if not security:
            return []

        if len(security) > 1:
            # log warning
            pass
        scope_ = security[0]["evesso"]
        if len(scope_) > 1:
            # log warning
            raise ValueError(
                f"API request with multiple scopes is not supported.\
                                Expect 1 scope, got {len(scope_)}"
            )
        return scope_

    def _load_metadata(self) -> None:
        """Load metadata from local file or EVE website.

        Synchronous request from EVE website if local file is not valid.

        Raises:
            ValueError: Metadata is empty when loading from local file or EVE website.
        """
        metadata = None
        if not os.path.exists(METADATA_PATH) or os.stat(METADATA_PATH).st_size == 0:
            r = requests.get(
                "https://esi.evetech.net/latest/swagger.json?datasource=tranquility"
            )
            r.raise_for_status()
            metadata = r.json()
            with open(METADATA_PATH, "w") as metadata_file:
                json.dump(metadata, metadata_file)
        else:
            with open(METADATA_PATH) as metadata_file:
                metadata = json.load(metadata_file)

        if not metadata or not metadata.keys():
            raise ValueError("Metadata is empty.")

        self.securityDefinitions = metadata["securityDefinitions"]
        self.paths = metadata["paths"]

        params = [
            Param(
                f"{v['name']}",
                v["in"],
                v.get("required", False),
                v["type"],
                v.get("default", None),
            )
            for v in metadata["parameters"].values()
        ]
        self._metaParams = ESIParams(params)

    # Helpful functions

    def print_names(
        self,
        _in: Optional[str] = None,
        required: Optional[bool] = None,
        default: Optional[bool] = None,
    ) -> None:
        """Prints out parameters' names with conditions.

        Used to find parameters with certain conditions. Only names (and other helpful message) are printed.
        Used for debugging.

        Args:
            _in: str
                Parameters with _in field different from the given _in is filtered.
                Default not filtering based on "_in" field.
            required: bool
                Select parameters with required field set to True or False.
                Default not filtering based on "required" field.
            default: bool
                Select parameters with or without a default value.
                Default not filtering based on "default" field.
        """
        error_header_printed = False
        ins = []  # hold results
        for key in self.paths.keys():
            try:
                for param in self.__getitem__(key).parameters:
                    if (
                        (_in and param._in != _in)
                        or param.name in ins
                        or (required is not None and param.required != required)
                        or (default and not param.default)
                    ):
                        continue
                    ins.append(param.name)
            except KeyError:
                if not error_header_printed:
                    print("Invalid APIs that does not follow filters: ")
                    error_header_printed = True
                print("\t", key)
                continue
        print(f"Names filtered for {_in}:")
        for i in ins:
            print(f"\t{i}")
        return

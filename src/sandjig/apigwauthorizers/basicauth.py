import logging
import os
import re
import sys
from base64 import b64decode
from binascii import Error as PaddingError
from urllib.parse import unquote

SALT = os.getenv("SALT", "sandjig-324789jh")

logging.basicConfig(
    stream=sys.stdout, level=logging.INFO, format="%(asctime)s [%(levelname)s] (%(name)s) %(funcName)s: %(message)s"
)

logger = logging.getLogger()

LOG_LEVEL = os.getenv("LOG_LEVEL", "DEBUG")
if LOG_LEVEL and LOG_LEVEL in ("INFO", "ERROR", "WARNING", "DEBUG", "CRITICAL"):
    level = getattr(logging, LOG_LEVEL)
    logger.setLevel(level)


class DecodeError(Exception):
    """Use to signal failed authorization token decode."""


class UnauthorizedError(Exception):
    """Use to signal unauthorized access to a resource."""


class HttpVerb:
    """Define HTTP Verbs for clarity"""

    GET = "GET"
    POST = "POST"
    PUT = "PUT"
    PATCH = "PATCH"
    HEAD = "HEAD"
    DELETE = "DELETE"
    OPTIONS = "OPTIONS"
    ALL = "*"


class AuthPolicy:
    """
    From awslabs blueprint
    https://github.com/awslabs/aws-apigateway-lambda-authorizer-blueprints/blob/master/blueprints/python/api-gateway-authorizer-python.py
    """

    # The AWS account id the policy will be generated for. This is used to create the method ARNs.
    awsAccountId = ""  # noqa: N815

    # The principal used for the policy, this should be a unique identifier for the end user.
    principalId = ""  # noqa: N815

    # The policy version used for the evaluation. This should always be '2012-10-17'
    version = "2012-10-17"

    # The regular expression used to validate resource paths for the policy
    pathRegex = r"^[/.a-zA-Z0-9-\*]+$"  # noqa: N815

    # these are the internal lists of allowed and denied methods. These are lists
    # of objects and each object has 2 properties: A resource ARN and a nullable
    # conditions statement.
    # the build method processes these lists and generates the appropriate
    # statements for the final policy
    allowMethods = []  # noqa: N815
    denyMethods = []  # noqa: N815

    # The API Gateway API id. By default this is set to '*'
    restApiId = "*"  # noqa: N815

    # The region where the API is deployed. By default this is set to '*'
    region = "*"

    # The name of the stage used in the policy. By default this is set to '*'
    stage = "*"

    def __init__(self, principal: str, awsAccountId: str) -> None:  # noqa: N803
        self.awsAccountId = awsAccountId
        self.principalId = principal
        self.allowMethods = []
        self.denyMethods = []

    def _addMethod(self, effect: str, verb: str, resource: str, conditions: list) -> None:  # noqa: N802
        """Adds a method to the internal lists of allowed or denied methods. Each object in
        the internal list contains a resource ARN and a condition statement. The condition
        statement can be null.
        """
        if verb != "*" and not hasattr(HttpVerb, verb):
            raise NameError("Invalid HTTP verb " + verb + ". Allowed verbs in HttpVerb class")
        resourcePattern = re.compile(self.pathRegex)  # noqa: N806
        if not resourcePattern.match(resource):
            raise NameError("Invalid resource path: " + resource + ". Path should match " + self.pathRegex)

        if resource.endswith("/"):
            resource = resource[1:]  # remove trailing slash, '/'

        resourceArn = (  # noqa: N806
            f"arn:aws:execute-api:{self.region}:{self.awsAccountId}:{self.restApiId}/{self.stage}/{verb}/{resource}"
        )
        logger.info(f"resourceArn: {resourceArn}")

        if effect.lower() == "allow":
            self.allowMethods.append({"resourceArn": resourceArn, "conditions": conditions})
        elif effect.lower() == "deny":
            self.denyMethods.append({"resourceArn": resourceArn, "conditions": conditions})

    def _getEmptyStatement(self, effect: str) -> dict:  # noqa: N802
        """Returns an empty statement object prepopulated with the correct action and the
        desired effect.
        """
        statement = {"Action": "execute-api:Invoke", "Effect": effect[:1].upper() + effect[1:].lower(), "Resource": []}

        return statement

    def _getStatementForEffect(self, effect: str, methods: list | None) -> list[dict]:  # noqa: N802
        """This function loops over an array of objects containing a resourceArn and
        conditions statement and generates the array of statements for the policy.
        """
        statements = []

        if methods:
            statement: dict = self._getEmptyStatement(effect)

            for curMethod in methods:  # noqa: N806
                if curMethod["conditions"] is None or len(curMethod["conditions"]) == 0:
                    statement["Resource"].append(curMethod["resourceArn"])
                else:
                    conditionalStatement: dict = self._getEmptyStatement(effect)  # noqa: N806
                    conditionalStatement["Resource"].append(curMethod["resourceArn"])
                    conditionalStatement["Condition"] = curMethod["conditions"]
                    statements.append(conditionalStatement)

            statements.append(statement)

        return statements

    def allowAllMethods(self) -> None:  # noqa: N802
        """Adds a '*' allow to the policy to authorize access to all methods of an API"""
        self._addMethod("Allow", HttpVerb.ALL, "*", [])

    def denyAllMethods(self) -> None:  # noqa: N802
        """Adds a '*' allow to the policy to deny access to all methods of an API"""
        self._addMethod("Deny", HttpVerb.ALL, "*", [])

    def allowMethod(self, verb: str, resource: str) -> None:  # noqa: N802
        """Adds an API Gateway method (Http verb + Resource path) to the list of allowed
        methods for the policy
        """
        self._addMethod("Allow", verb, resource, [])

    def denyMethod(self, verb: str, resource: str) -> None:  # noqa: N802
        """Adds an API Gateway method (Http verb + Resource path) to the list of denied
        methods for the policy
        """
        self._addMethod("Deny", verb, resource, [])

    def allowMethodWithConditions(self, verb: str, resource: str, conditions: list) -> None:  # noqa: N802
        """Adds an API Gateway method (Http verb + Resource path) to the list of allowed
        methods and includes a condition for the policy statement. More on AWS policy
        conditions here: http://docs.aws.amazon.com/IAM/latest/UserGuide/reference_policies_elements.html#Condition
        """
        self._addMethod("Allow", verb, resource, conditions)

    def denyMethodWithConditions(self, verb: str, resource: str, conditions: list) -> None:  # noqa: N802
        """Adds an API Gateway method (Http verb + Resource path) to the list of denied
        methods and includes a condition for the policy statement. More on AWS policy
        conditions here: http://docs.aws.amazon.com/IAM/latest/UserGuide/reference_policies_elements.html#Condition
        """
        self._addMethod("Deny", verb, resource, conditions)

    def build(self) -> dict:
        """Generates the policy document based on the internal lists of allowed and denied
        conditions. This will generate a policy with two main statements for the effect:
        one statement for Allow and one statement for Deny.
        Methods that includes conditions will have their own statement in the policy.
        """
        if (self.allowMethods is None or len(self.allowMethods) == 0) and (
            self.denyMethods is None or len(self.denyMethods) == 0
        ):
            raise NameError("No statements defined for the policy")

        policy = {"principalId": self.principalId, "policyDocument": {"Version": self.version, "Statement": []}}

        policy["policyDocument"]["Statement"].extend(self._getStatementForEffect("Allow", self.allowMethods))
        policy["policyDocument"]["Statement"].extend(self._getStatementForEffect("Deny", self.denyMethods))

        return policy


def parse_arn(arn_str: str) -> tuple[str, str, str, str | None, str | None, list[str] | None]:
    """
    https://docs.aws.amazon.com/general/latest/gr/aws-arns-and-namespaces.html
    arn:partition:service:region:account-id:resource
    arn:partition:service:region:account-id:resourcetype/resource
    arn:partition:service:region:account-id:resourcetype/resource/qualifier
    arn:partition:service:region:account-id:resourcetype/resource:qualifier
    arn:partition:service:region:account-id:resourcetype:resource
    arn:partition:service:region:account-id:resourcetype:resource:qualifier

    :return: service, region, account_id, resource_type, resource, qualifier
    """
    *_, service, region, account_id, resource_elements = arn_str.split(":", 5)

    resource_type = None
    resource = None
    qualifiers = None
    if not any(separator in resource_elements for separator in (":", "/")):
        resource = resource_elements
    elif ":" in resource_elements and "/" not in resource_elements:
        if resource_elements.count(":") == 1:  # noqa: PLR2004
            resource_type, resource = resource_elements.split(":")
        elif resource_elements.count(":") >= 2:  # noqa: PLR2004
            resource_type, resource, *qualifiers = resource_elements.split(":")
    elif ":" not in resource_elements and "/" in resource_elements:
        if resource_elements.count("/") == 1:  # noqa: PLR2004
            resource_type, resource = resource_elements.split("/")
        elif resource_elements.count("/") >= 2:  # noqa: PLR2004
            resource_type, resource, *qualifiers = resource_elements.split("/")
    elif ":" in resource_elements and "/" in resource_elements:
        # 'arn:partition:service:region:account-id:resourcetype/resource:qualifier'
        resource_type, remaining = resource_elements.split("/")
        resource, qualifier = remaining.split(":")
        qualifiers = [qualifier]

    return service, region, account_id, resource_type, resource, qualifiers


def basicauth_decode(encoded_str: str) -> tuple[str, str]:
    """
    Decode an encrypted HTTP basic authentication string. Returns a tuple of
    the form (username, password), and raises a DecodeError exception if
    nothing could be decoded.
    """
    split = encoded_str.strip().split(" ")

    # If split is only one element, try to decode the username and password
    # directly.
    split_by_colon_length = 1
    split_by_colon_with_basic_prefix_length = 2
    if len(split) == split_by_colon_length:
        try:
            auth_component = split[0]
            decoded_string = b64decode(auth_component).decode()  # possible PaddingError
            username, password = decoded_string.split(":", 1)  # possible ValueError
            return unquote(username), unquote(password)
        except (PaddingError, ValueError) as e:
            raise DecodeError from e

    # If there are only two elements, check the first and ensure it says
    # 'basic' so that we know we're about to decode the right thing. If not,
    # bail out.
    elif len(split) == split_by_colon_with_basic_prefix_length:
        if split[0].strip().lower() == "basic":
            try:
                auth_component = split[1]
                decoded_string = b64decode(auth_component).decode()
                username, password = decoded_string.split(":", 1)
                return unquote(username), unquote(password)
            except (PaddingError, ValueError) as e:
                raise DecodeError from e
        else:
            raise DecodeError

    # If there are more than 2 elements, something crazy must be happening.
    # Bail.
    logger.debug(f"len(split_values) > 2: {split}")
    raise DecodeError


def check_basicauth_header_authorization_handler(event: dict, context: dict) -> dict:  # noqa: ARG001
    """Confirm that the request has the expected BasicAuthorization Header"""
    BASIC_AUTH_USERNAME = os.getenv("BASIC_AUTH_USERNAME", None)  # noqa: N806
    BASIC_AUTH_PASSWORD = os.getenv("BASIC_AUTH_PASSWORD", None)  # noqa: N806
    if BASIC_AUTH_PASSWORD is None:
        logger.error("BASIC_AUTH_USERNAME is None, INVALID CONFIGURATION!")
    if BASIC_AUTH_PASSWORD is None:
        logger.error("BASIC_AUTH_PASSWORD is None, INVALID CONFIGURATION!")

    authorization_header = event["headers"].get("Authorization", event["headers"].get("authorization", None))
    http_method = event["requestContext"].get("httpMethod", None)
    allowed_methods = ("OPTIONS",)
    # prepare reference principal_id
    # https://docs.aws.amazon.com/AmazonS3/latest/dev/s3-bucket-user-policy-specifying-principal-intro.html
    principal_id = "*"
    if http_method not in allowed_methods:
        username = None
        password = None
        if not authorization_header:
            logger.warning("Authorization Header not given!")
            logger.warning(f"headers: {event['headers']}")
            raise UnauthorizedError("Unauthorized")  # Raises 401 response from API Gateway
        try:
            username, password = basicauth_decode(authorization_header)
        except DecodeError as e:
            # return 400 error
            logger.exception(f"DecodeError: {e.args}")
            raise UnauthorizedError("Unauthorized") from e  # Raises 401 response from API Gateway

        if username != BASIC_AUTH_USERNAME:
            logger.info("username=%s", username)
            raise UnauthorizedError("Unauthorized")  # Raises 401 response from API Gateway
        if password != BASIC_AUTH_PASSWORD:
            raise UnauthorizedError("Unauthorized")  # Raises 401 response from API Gateway

    # arn:partition:service:region:account-id:resourcetype/resource/qualifier
    # arn:aws:execute-api:region:account-id:api-id/stage-name/HTTP-VERB/resource-path
    # api-id = resource_type
    # stage-name = resource
    method_arn = event["methodArn"]
    logger.info(f"Parsing methodArn({event['methodArn']}) ...")
    service, region, account_id, resource_type, resource, qualifiers = parse_arn(method_arn)

    policy = AuthPolicy(principal_id, account_id)
    policy.restApiId = resource_type or "*"
    policy.stage = resource or "*"
    policy.allowAllMethods()

    authorization_response = policy.build()
    # add additional key-value pairs associated with the authenticated principal
    # these are made available by APIGW like so: $context.authorizer.<key>
    # additional context is cached
    # context = {'key': 'value', ...  }
    return authorization_response

import os
from unittest import TestCase, mock

import pytest

from sandjig.apigwauthorizers.basicauth import check_basicauth_header_authorization_handler

TEST_USERNAME = "runningman"
TEST_PASSWORD = "arnold-1987"  # noqa: S105
TEST_BASICAUTH = "Basic cnVubmluZ21hbjphcm5vbGQtMTk4Nw=="  # noqa: S105


def username(*_):
    return TEST_USERNAME


class BasicAuthHandlerTestCase(TestCase):
    def test_httpmethod__options__no__authorization(self):
        event = {
            "type": "REQUEST",
            "methodArn": "arn:aws:execute-api:us-east-1:123456789012:s4x3opwd6i/test/*/request",
            "resource": "/request",
            "path": "/request",
            "httpMethod": "ANY",
            "headers": {
                "X-AMZ-Date": "20170718T062915Z",
                "Accept": "*/*",
                "headerauth1": "headerValue1",
                "CloudFront-Viewer-Country": "US",
                "CloudFront-Forwarded-Proto": "https",
                "CloudFront-Is-Tablet-Viewer": "false",
                "CloudFront-Is-Mobile-Viewer": "false",
                "User-Agent": "...",
                "X-Forwarded-Proto": "https",
                "CloudFront-Is-SmartTV-Viewer": "false",
                "Host": "....execute-api.us-east-1.amazonaws.com",
                "Accept-Encoding": "gzip, deflate",
                "X-Forwarded-Port": "443",
                "X-Amzn-Trace-Id": "...",
                "Via": "...cloudfront.net (CloudFront)",
                "X-Amz-Cf-Id": "...",
                "X-Forwarded-For": "..., ...",
                "Postman-Token": "...",
                "cache-control": "no-cache",
                "CloudFront-Is-Desktop-Viewer": "true",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            "queryStringParameters": {"QueryString1": "queryValue1"},
            "pathParameters": {},
            "stageVariables": {"StageVar1": "stageValue1"},
            "requestContext": {
                "path": "/request",
                "accountId": "123456789012",
                "resourceId": "05c7jb",
                "stage": "test",
                "requestId": "...",
                "identity": {"apiKey": "...", "sourceIp": "..."},
                "resourcePath": "/request",
                "httpMethod": "OPTIONS",
                "apiId": "s4x3opwd6i",
            },
        }
        response = check_basicauth_header_authorization_handler(event=event, context={})
        self.assertTrue(response)

    @mock.patch.dict(os.environ, {"BASIC_AUTH_USERNAME": TEST_USERNAME, "BASIC_AUTH_PASSWORD": TEST_PASSWORD})
    def test_httpmethod__get__valid__authorization__uppercase(self, *_):
        event = {
            "type": "REQUEST",
            "methodArn": "arn:aws:execute-api:us-east-1:123456789012:s4x3opwd6i/test/GET/request",
            "resource": "/request",
            "path": "/request",
            "httpMethod": "GET",
            "headers": {
                "X-AMZ-Date": "20170718T062915Z",
                "Accept": "*/*",
                "Authorization": TEST_BASICAUTH,
                "CloudFront-Viewer-Country": "US",
                "CloudFront-Forwarded-Proto": "https",
                "CloudFront-Is-Tablet-Viewer": "false",
                "CloudFront-Is-Mobile-Viewer": "false",
                "User-Agent": "...",
                "X-Forwarded-Proto": "https",
                "CloudFront-Is-SmartTV-Viewer": "false",
                "Host": "....execute-api.us-east-1.amazonaws.com",
                "Accept-Encoding": "gzip, deflate",
                "X-Forwarded-Port": "443",
                "X-Amzn-Trace-Id": "...",
                "Via": "...cloudfront.net (CloudFront)",
                "X-Amz-Cf-Id": "...",
                "X-Forwarded-For": "..., ...",
                "Postman-Token": "...",
                "cache-control": "no-cache",
                "CloudFront-Is-Desktop-Viewer": "true",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            "queryStringParameters": {"QueryString1": "queryValue1"},
            "pathParameters": {},
            "stageVariables": {"StageVar1": "stageValue1"},
            "requestContext": {
                "path": "/request",
                "accountId": "123456789012",
                "resourceId": "05c7jb",
                "stage": "test",
                "requestId": "...",
                "identity": {"apiKey": "...", "sourceIp": "..."},
                "resourcePath": "/request",
                "httpMethod": "GET",
                "apiId": "s4x3opwd6i",
            },
        }
        response = check_basicauth_header_authorization_handler(event=event, context={})
        self.assertTrue(response)

    @mock.patch.dict(os.environ, {"BASIC_AUTH_USERNAME": TEST_USERNAME, "BASIC_AUTH_PASSWORD": TEST_PASSWORD})
    def test_httpmethod__get__valid__authorization__lowercase(self, *_):
        event = {
            "type": "REQUEST",
            "methodArn": "arn:aws:execute-api:us-east-1:123456789012:s4x3opwd6i/test/GET/request",
            "resource": "/request",
            "path": "/request",
            "httpMethod": "GET",
            "headers": {
                "X-AMZ-Date": "20170718T062915Z",
                "Accept": "*/*",
                "authorization": TEST_BASICAUTH,
                "CloudFront-Viewer-Country": "US",
                "CloudFront-Forwarded-Proto": "https",
                "CloudFront-Is-Tablet-Viewer": "false",
                "CloudFront-Is-Mobile-Viewer": "false",
                "User-Agent": "...",
                "X-Forwarded-Proto": "https",
                "CloudFront-Is-SmartTV-Viewer": "false",
                "Host": "....execute-api.us-east-1.amazonaws.com",
                "Accept-Encoding": "gzip, deflate",
                "X-Forwarded-Port": "443",
                "X-Amzn-Trace-Id": "...",
                "Via": "...cloudfront.net (CloudFront)",
                "X-Amz-Cf-Id": "...",
                "X-Forwarded-For": "..., ...",
                "Postman-Token": "...",
                "cache-control": "no-cache",
                "CloudFront-Is-Desktop-Viewer": "true",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            "queryStringParameters": {"QueryString1": "queryValue1"},
            "pathParameters": {},
            "stageVariables": {"StageVar1": "stageValue1"},
            "requestContext": {
                "path": "/request",
                "accountId": "123456789012",
                "resourceId": "05c7jb",
                "stage": "test",
                "requestId": "...",
                "identity": {"apiKey": "...", "sourceIp": "..."},
                "resourcePath": "/request",
                "httpMethod": "GET",
                "apiId": "s4x3opwd6i",
            },
        }
        response = check_basicauth_header_authorization_handler(event=event, context={})
        self.assertTrue(response)

    @mock.patch.dict(os.environ, {"BASIC_AUTH_USERNAME": TEST_USERNAME, "BASIC_AUTH_PASSWORD": TEST_PASSWORD})
    def test_httpmethod__get__invalid__authorization(self, *_):
        event = {
            "type": "REQUEST",
            "methodArn": "arn:aws:execute-api:us-east-1:123456789012:s4x3opwd6i/test/GET/request",
            "resource": "/request",
            "path": "/request",
            "httpMethod": "GET",
            "headers": {
                "X-AMZ-Date": "20170718T062915Z",
                "Accept": "*/*",
                "Authorization": "Basic Xomthing",
                "CloudFront-Viewer-Country": "US",
                "CloudFront-Forwarded-Proto": "https",
                "CloudFront-Is-Tablet-Viewer": "false",
                "CloudFront-Is-Mobile-Viewer": "false",
                "User-Agent": "...",
                "X-Forwarded-Proto": "https",
                "CloudFront-Is-SmartTV-Viewer": "false",
                "Host": "....execute-api.us-east-1.amazonaws.com",
                "Accept-Encoding": "gzip, deflate",
                "X-Forwarded-Port": "443",
                "X-Amzn-Trace-Id": "...",
                "Via": "...cloudfront.net (CloudFront)",
                "X-Amz-Cf-Id": "...",
                "X-Forwarded-For": "..., ...",
                "Postman-Token": "...",
                "cache-control": "no-cache",
                "CloudFront-Is-Desktop-Viewer": "true",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            "queryStringParameters": {"QueryString1": "queryValue1"},
            "pathParameters": {},
            "stageVariables": {"StageVar1": "stageValue1"},
            "requestContext": {
                "path": "/request",
                "accountId": "123456789012",
                "resourceId": "05c7jb",
                "stage": "test",
                "requestId": "...",
                "identity": {"apiKey": "...", "sourceIp": "..."},
                "resourcePath": "/request",
                "httpMethod": "GET",
                "apiId": "s4x3opwd6i",
            },
        }
        with pytest.raises(Exception):  # noqa: B017
            check_basicauth_header_authorization_handler(event=event, context={})

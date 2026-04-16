class CloudformationWaiterMock:
    def wait(self, *args, **kwargs) -> bool:
        return True

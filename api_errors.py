class DataError(ValueError):
    """來源資料或連線錯誤。"""


class ApiLimitError(DataError):
    """API 額度／權限限制，停止整批掃描，保留既有成果。"""

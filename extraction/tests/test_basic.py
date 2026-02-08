import pytest
from collector import DataCollector

def test_data_collector():
    collector = DataCollector()
    data = collector.collect_raw_data()
    assert isinstance(data, list)
    assert len(data) > 0
    assert "id" in data[0]
    assert "content" in data[0]

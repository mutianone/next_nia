import pytest
from pydantic import ValidationError
from backend.app.schemas import GeneralSettings, Settings
from backend.app.config import SettingsStore


def test_locations_normalize_and_persist(tmp_path):
    settings = Settings()
    settings.general = GeneralSettings(interview_locations=[' 第一询问室 ', '', '第一询问室', '第二询问室'])
    store = SettingsStore(tmp_path)
    store.save(settings)
    assert store.load().general.interview_locations == ['第一询问室', '第二询问室']
    assert GeneralSettings().interview_locations == []


@pytest.mark.parametrize('locations', [['a' * 501], ['地点\n第二行'], ['a'] * 101])
def test_invalid_locations_rejected(locations):
    with pytest.raises(ValidationError):
        GeneralSettings(interview_locations=locations)

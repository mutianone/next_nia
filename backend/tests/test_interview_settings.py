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


def test_external_permissions_default_and_persistence(tmp_path):
    from backend.app.schemas import SettingsUpdate, merge_settings, public_settings
    settings = Settings()
    assert settings.ai.external_permissions.model_dump() == {'question': True, 'asr': True, 'analysis': True}
    settings.ai.external_permissions.question = False
    store = SettingsStore(tmp_path)
    store.save(settings)
    assert store.load().ai.external_permissions.question is False
    assert public_settings(settings)['ai']['external_permissions']['question'] is False
    payload = settings.model_dump(mode='json')
    del payload['ai']['external_permissions']
    assert merge_settings(SettingsUpdate.model_validate(payload), settings).ai.external_permissions.question is False

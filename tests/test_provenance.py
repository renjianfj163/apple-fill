from apple_fill.provenance import normalize_text, value_supported_by_quote


def test_fullwidth_voltage_matches():
    assert value_supported_by_quote("２２０Ｖ", "额定电压：220V")
    assert normalize_text("220伏") == "220v"


def test_shorter_number_is_not_the_same_value():
    assert value_supported_by_quote("220V", "额定电压：220V")
    assert not value_supported_by_quote("20", "额定电压：220V")
    assert not value_supported_by_quote("20V", "额定电压：220V")
    assert not value_supported_by_quote("110V", "额定电压：220V")


def test_product_name_must_be_the_whole_field():
    quote = "产品名称：青苹空气净化器"
    assert value_supported_by_quote("青苹空气净化器", quote)
    assert not value_supported_by_quote("净化器", quote)

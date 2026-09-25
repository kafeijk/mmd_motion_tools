from common.i18n.dictionary import preprocess_dictionary

dictionary = {
    "zh_CN": {
        ("*", "Model directory not found!"): "模型目录不存在！",
    }
}

dictionary = preprocess_dictionary(dictionary)

dictionary["zh_HANS"] = dictionary["zh_CN"]

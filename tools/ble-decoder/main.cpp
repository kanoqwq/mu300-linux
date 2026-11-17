// SPDX-License-Identifier: GPL-3.0-or-later
// Separate JSON-line bridge to Theengs Decoder. No radio or filesystem access.
#include <iostream>
#include <string>
#include "decoder.h"

int main() {
    TheengsDecoder decoder;
    std::string line;
    while (std::getline(std::cin, line)) {
        if (line.size() > 8192) { std::cout << "{}" << std::endl; continue; }
        DynamicJsonDocument input(16384);
        if (deserializeJson(input, line) || !input.is<JsonObject>()) {
            std::cout << "{}" << std::endl; continue;
        }
        JsonObject data = input.as<JsonObject>();
        if (decoder.decodeBLEJson(data) < 0) { std::cout << "{}" << std::endl; continue; }
        const char* model = data["model_id"] | "";
        std::string properties = decoder.getTheengProperties(model);
        DynamicJsonDocument output(16384), schema(8192);
        output["data"].set(data);
        if (!deserializeJson(schema, properties)) output["properties"].set(schema.as<JsonVariant>());
        serializeJson(output, std::cout);
        std::cout << std::endl;
    }
    return 0;
}

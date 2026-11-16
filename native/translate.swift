// Murmur's on-device translator.
//
// A long-lived JSON-lines worker around Apple's Translation framework:
//   {"id":1,"text":"…","source":"auto","target":"es"}
//   {"id":1,"text":"…","source":"en","translated":true}
//
// Build: swiftc -O -parse-as-library -framework Translation -framework NaturalLanguage \
//        native/translate.swift -o bin/murmur-translate

import Foundation
import NaturalLanguage
import Translation

struct Request: Decodable {
    let id: Int
    let text: String
    let source: String?
    let target: String
}

func base(_ code: String) -> String {
    String(code.split(separator: "-").first ?? Substring(code))
}

func emit(_ object: [String: Any]) {
    guard let data = try? JSONSerialization.data(withJSONObject: object),
          let line = String(data: data, encoding: .utf8) else { return }
    FileHandle.standardOutput.write((line + "\n").data(using: .utf8)!)
}

func detect(_ text: String) -> String {
    let recognizer = NLLanguageRecognizer()
    recognizer.processString(text)
    guard let language = recognizer.dominantLanguage else { return "und" }
    return base(language.rawValue)
}

@main
struct TranslatorServer {
    static func main() async {
        let availability = LanguageAvailability()
        var sessions: [String: TranslationSession] = [:]

        while let line = readLine() {
            guard let data = line.data(using: .utf8),
                  let request = try? JSONDecoder().decode(Request.self, from: data) else { continue }

            let text = request.text
            let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
            if trimmed.isEmpty {
                emit(["id": request.id, "text": text, "source": "und", "translated": false])
                continue
            }

            let sourceCode = (request.source?.isEmpty == false && request.source != "auto")
                ? base(request.source!)
                : detect(trimmed)
            let targetCode = base(request.target)

            if sourceCode == "und" || sourceCode == targetCode {
                emit(["id": request.id, "text": text, "source": sourceCode, "translated": false])
                continue
            }

            let key = "\(sourceCode)->\(targetCode)"
            do {
                if sessions[key] == nil {
                    let source = Locale.Language(identifier: sourceCode)
                    let target = Locale.Language(identifier: targetCode)
                    let status = await availability.status(from: source, to: target)
                    guard status == .installed else {
                        emit([
                            "id": request.id,
                            "error": "not-installed",
                            "status": "\(status)",
                            "source": sourceCode,
                        ])
                        continue
                    }
                    sessions[key] = TranslationSession(installedSource: source, target: target)
                }
                let response = try await sessions[key]!.translate(text)
                emit([
                    "id": request.id,
                    "text": response.targetText,
                    "source": sourceCode,
                    "translated": true,
                ])
            } catch {
                emit(["id": request.id, "error": "\(error)", "source": sourceCode])
            }
        }
    }
}

// Murmur's on-device prompt writer.
//
// A JSON-lines worker over Apple's Foundation Models:
//   {"id":1,"op":"bible","text":"<chapter sample>"}
//   {"id":2,"op":"prompt","text":"<beat>","before":"…","after":"…","bible":"…","shot":"close-up"}
//
// Build: swiftc -O -parse-as-library -framework FoundationModels \
//        native/prompts.swift -o bin/murmur-prompts

import Foundation
import FoundationModels

struct Request: Decodable {
    let id: Int
    let op: String
    let text: String
    let before: String?
    let after: String?
    let bible: String?
    let shot: String?
}

func emit(_ object: [String: Any]) {
    guard let data = try? JSONSerialization.data(withJSONObject: object),
          let line = String(data: data, encoding: .utf8) else { return }
    FileHandle.standardOutput.write((line + "\n").data(using: .utf8)!)
}

func clip(_ text: String, _ limit: Int) -> String {
    let clean = text.replacingOccurrences(of: "\n", with: " ")
        .trimmingCharacters(in: .whitespacesAndNewlines)
    return String(clean.prefix(limit))
}

@main
struct PromptServer {
    static func main() async {
        let model = SystemLanguageModel.default
        guard case .available = model.availability else {
            FileHandle.standardError.write(
                "Foundation Models is unavailable on this Mac.\n".data(using: .utf8)!
            )
            return
        }

        while let line = readLine() {
            guard let data = line.data(using: .utf8),
                  let request = try? JSONDecoder().decode(Request.self, from: data)
            else { continue }

            let instructions = """
            You are the art director for a monochrome manga adaptation of a book. You write \
            concise, visually specific prompts for a black-and-white text-to-image model. \
            Use ink linework, screentone shading, dramatic framing and high contrast. Never \
            use colour names, speech bubbles, captions or lettering. Do not describe faces, \
            hair, age, skin tone or clothing: the character reference supplies those details. \
            Describe only action, framing, location, lighting and mood. Never add commentary, \
            headings, quotes, or explanations — only the requested text.
            """

            let content: String
            if request.op == "bible" {
                content = """
                Passage:
                \(clip(request.text, 2600))

                Write a story bible for a monochrome manga adaptation of this passage. Use only \
                details explicitly stated in the passage; do not invent glyphs, writing, signs, \
                symbols, or extra characters. In 3-5 sentences, cover the recurring characters \
                (age, build, hair, clothing, distinguishing features), the main locations, the era, \
                and the recurring visual motifs. If a detail is missing, choose a simple, consistent \
                design and state it. Describe everything as black-and-white ink drawings with \
                screentone; do not name colours.
                """
            } else if request.op == "cast" {
                content = """
                Passage:
                \(clip(request.text, 5000))

                List the recurring characters in this passage, one per line, in this exact format:
                Name | protagonist or ally | age, build, face, hair, clothing, distinguishing \
                features.
                The first line must be the first-person narrator, named "Narrator" if unnamed. \
                Include at most two characters who appear repeatedly. Use only details stated in \
                the passage; if a detail is missing, choose a simple, consistent design. Do not \
                include locations, objects, vehicles, colours, or commentary. No headings, no \
                bullet points.
                """
            } else {
                content = """
                Story bible:
                \(request.bible ?? "A cinematic adaptation of the book.")

                Previous beat: \(clip(request.before ?? "", 240))
                Current beat: \(request.text)
                Next beat: \(clip(request.after ?? "", 240))
                Required shot: \(request.shot ?? "director's choice")

                Write ONE prompt for a single full-bleed monochrome manga illustration that \
                captures the essence of the current beat. Choose ONE decisive moment and one \
                camera angle; do not show multiple moments, a montage, or a comic page. Use the \
                required shot; do not default to a wide low-angle shot unless that is the \
                required shot. Include the recurring character(s), their action, the key object \
                or emotion, location, and lighting. Do not describe faces, hair, age, skin or \
                clothing; the character reference supplies those. Keep the same world as the \
                bible. Black ink linework, screentone, high contrast. 30-55 words. Plain \
                surfaces only: no writing, no lettering, no signs, no glyphs, no captions, no \
                speech bubbles, no panel borders, no gutters, no split screen. Return only the \
                prompt.
                """
            }

            do {
                let session = LanguageModelSession(instructions: instructions)
                let response = try await session.respond(to: content)
                var text = response.content.trimmingCharacters(in: .whitespacesAndNewlines)
                if text.hasPrefix("\"") && text.hasSuffix("\"") && text.count > 2 {
                    text = String(text.dropFirst().dropLast())
                }
                emit(["id": request.id, "text": text])
            } catch {
                emit(["id": request.id, "error": "\(error)"])
            }
        }
    }
}

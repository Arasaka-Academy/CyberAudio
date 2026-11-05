import Foundation
import Darwin
import CyberAudioCore
import CyberAudioInference

@main enum ServiceCommand {
    static func main() {
        umask(0o077)
        do {
            let args = Array(CommandLine.arguments.dropFirst())
            guard args.count%2 == 0 else { throw ServiceError.inference }
            var options: [String:String] = [:]
            for i in stride(from:0,to:args.count,by:2) {
                guard ["--root","--parent-pid","--instance","--metallib"].contains(args[i]),options[args[i]] == nil else { throw ServiceError.inference }
                options[args[i]] = args[i+1]
            }
            guard let path = options["--root"],let parent = options["--parent-pid"].flatMap(Int32.init),parent == getppid(),
                  let instance = options["--instance"],let metalPath = options["--metallib"] else { throw ServiceError.inference }
            let root = URL(fileURLWithPath:path,isDirectory:true)
            let token = try String(contentsOf:root.appendingPathComponent("config/api-token"),encoding:.utf8).trimmingCharacters(in:.whitespacesAndNewlines)
            let settings = try ServiceSettings(root:root,configuration:AssetStore.configuration(root:root),token:token,instance:instance)
            let lockFD = open(root.appendingPathComponent("config/backend.lock").path,O_CREAT|O_RDWR,0o600)
            guard lockFD >= 0,flock(lockFD,LOCK_EX|LOCK_NB) == 0 else { throw ServiceError.inference }
            defer { close(lockFD) }
            try AudioFile.cleanup(settings.temporaryDirectory)
            let log = try ServiceLog(root:root)
            let coordinator = ServiceCoordinator(settings:settings,log:log.write)
            let server = HTTPService(coordinator:coordinator)
            _ = try server.start()
            log.write("service_started backend=mlx-swift")
            let stopped = DispatchSemaphore(value:0)
            let shutdown = ShutdownGate(coordinator:coordinator,stopped:stopped)
            signal(SIGTERM,SIG_IGN); signal(SIGINT,SIG_IGN); signal(SIGPIPE,SIG_IGN)
            let term = DispatchSource.makeSignalSource(signal:SIGTERM,queue:.global()); term.setEventHandler { shutdown.stop() }; term.resume()
            let interrupt = DispatchSource.makeSignalSource(signal:SIGINT,queue:.global()); interrupt.setEventHandler { shutdown.stop() }; interrupt.resume()
            let parentTimer = DispatchSource.makeTimerSource(queue:.global()); parentTimer.schedule(deadline:.now(),repeating:1)
            parentTimer.setEventHandler { if getppid() != parent { shutdown.stop() } }; parentTimer.resume()
            DispatchQueue.global().async {
                var bytes = [UInt8](repeating:0,count:64)
                while read(STDIN_FILENO,&bytes,bytes.count)>0 {}
                shutdown.stop()
            }
            Task.detached(priority:.userInitiated) {
                do {
                    let engine = try await QwenEngine.load(settings:settings,metallib:URL(fileURLWithPath:metalPath))
                    coordinator.worker.async { coordinator.install(engine); log.write("models_ready backend=mlx-swift") }
                } catch { coordinator.initializationFailed(error) }
            }
            stopped.wait()
            try server.stop()
            let joined = DispatchSemaphore(value:0); coordinator.worker.async { joined.signal() }
            _ = joined.wait(timeout:.now()+3)
            withExtendedLifetime([term,interrupt,parentTimer]) {}
            log.write("service_stopped")
        } catch {
            fputs("CyberAudioService: initialization or ownership failed; inspect configuration and service.log.\n",stderr)
            exit(1)
        }
    }
}

private final class ServiceLog: @unchecked Sendable {
    private let file: URL
    private let lock = NSLock()
    init(root: URL) throws {
        let directory = root.appendingPathComponent("logs")
        try FileManager.default.createDirectory(at:directory,withIntermediateDirectories:true)
        file = directory.appendingPathComponent("service.log")
    }
    func write(_ text: String) {
        lock.lock(); defer { lock.unlock() }
        if ((try? FileManager.default.attributesOfItem(atPath:file.path)[.size] as? NSNumber)?.intValue ?? 0) > 5*1024*1024 {
            for number in stride(from:4,through:1,by:-1) {
                let target = file.appendingPathExtension(String(number))
                try? FileManager.default.removeItem(at:target)
                let previous = number == 1 ? file : file.appendingPathExtension(String(number-1))
                try? FileManager.default.moveItem(at:previous,to:target)
            }
        }
        if !FileManager.default.fileExists(atPath:file.path) { FileManager.default.createFile(atPath:file.path,contents:nil,attributes:[.posixPermissions:0o600]) }
        guard let handle = try? FileHandle(forWritingTo:file) else { return }
        defer { try? handle.close() }
        _ = try? handle.seekToEnd()
        try? handle.write(contentsOf:Data((ISO8601DateFormatter().string(from:Date())+" "+text+"\n").utf8))
    }
}

private final class ShutdownGate: @unchecked Sendable {
    private let lock = NSLock()
    private var stopping = false
    private let coordinator: ServiceCoordinator
    private let stopped: DispatchSemaphore
    init(coordinator: ServiceCoordinator,stopped: DispatchSemaphore) { self.coordinator = coordinator; self.stopped = stopped }
    func stop() {
        lock.lock(); defer { lock.unlock() }
        guard !stopping else { return }; stopping = true
        coordinator.shutdown(); stopped.signal()
        // Metal/decoder calls can be uninterruptible; app ownership has a hard bound.
        DispatchQueue.global().asyncAfter(deadline:.now()+8) { _exit(0) }
    }
}

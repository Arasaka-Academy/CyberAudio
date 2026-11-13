import Foundation

/// Test-only Assets boundary. The CLI tests exercise the actual verified store;
/// this fixture controls long downloads and candidate failures in the App.
enum ModelManagementFixture {
    static func create(root: URL, port: Int, storage: String? = nil) throws -> URL {
        let fm = FileManager.default
        try fm.createDirectory(at:root.appendingPathComponent("config"),withIntermediateDirectories:true)
        let directory = storage ?? root.appendingPathComponent("models").path
        let catalog = URL(fileURLWithPath:fm.currentDirectoryPath).appendingPathComponent("Native/Sources/CyberAudioCore/ModelCatalog.lock.json")
        let registry = try JSONSerialization.jsonObject(with:Data(contentsOf:catalog)) as! [String:Any]
        let models: [[String:Any]] = (registry["models"] as! [[String:Any]]).map { item in
            var item = item
            let total = (item.removeValue(forKey:"files") as! [[String:Any]]).reduce(Int64(0)) { $0 + ($1["size"] as! NSNumber).int64Value }
            let id = item["id"] as! String
            let ready = id == "qwen3-asr-1.7b" || id == "qwen3-tts-1.7b" || id.hasSuffix("0.6b-4bit")
            let partial = id.hasSuffix("0.6b-8bit")
            item["sources"] = Array((item["sources"] as! [String:Any]).keys).sorted()
            item["total_bytes"] = total
            item["directory"] = directory + "/" + (item["repository"] as! String).split(separator:"/").last!
            item["state"] = ready ? "ready" : (partial ? "partial" : "missing")
            item["installed_bytes"] = ready ? total : 0
            item["verified_bytes"] = ready ? total : 0
            item["resumable_bytes"] = partial ? total / 4 : 0
            item["invalid_partials"] = [String]()
            item["active"] = id == "qwen3-asr-1.7b" || id == "qwen3-tts-1.7b"
            return item
        }
        try JSONSerialization.data(withJSONObject:models).write(to:root.appendingPathComponent("models.fixture.json"))
        let config = ServiceConfiguration(asr_dir:directory + "/Qwen3-ASR-1.7B-8bit",
            tts_dir:directory + "/Qwen3-TTS-12Hz-1.7B-CustomVoice-8bit",ffmpeg:"/opt/homebrew/bin/ffmpeg",port:port,https_url:"")
        try JSONEncoder().encode(config).write(to:root.appendingPathComponent("config/service.json"))
        try Data(String(repeating:"f",count:64).utf8).write(to:root.appendingPathComponent("config/api-token"))
        let helper = root.appendingPathComponent("fixture-assets")
        let script = #"""
        #!/usr/bin/python3
        import json, pathlib, sys, time
        command = sys.argv[1]
        options = dict(zip(sys.argv[2::2],sys.argv[3::2]))
        root = pathlib.Path(options['--root'])
        config_file = root/'config/service.json'
        config = json.loads(config_file.read_text())
        models_file = root/'models.fixture.json'
        models = json.loads(models_file.read_text())
        original = root/'original.fixture.json'
        def emit(record): print(json.dumps(record),flush=True)
        with (root/'commands.fixture.log').open('a') as log: log.write(command+'\n')
        if command == 'models':
            for model in models:
                model['active'] = model['id'] in [config.get('asr_model_id','qwen3-asr-1.7b'),config.get('tts_model_id','qwen3-tts-1.7b')]
            emit(dict(event='models',models=models,asr_model_id=config.get('asr_model_id','qwen3-asr-1.7b'),
                tts_model_id=config.get('tts_model_id','qwen3-tts-1.7b'),models_directory=config.get('models_dir',models[0]['directory'].rsplit('/',1)[0]),
                occupied_bytes=sum(model['installed_bytes'] for model in models),available_bytes=30000000000,pending_change=False))
        elif command == 'status':
            emit(dict(event='status',total_bytes=100,verified_bytes=100,resumable_bytes=0,missing_files=0,invalid_files=[],invalid_partials=[],
                models_ready=True,ffmpeg_ready=True,ffmpeg_path='/ffmpeg',available_bytes=30000000000,required_bytes=0,can_download=True))
        elif command == 'download':
            model = next(model for model in models if model['id']==options['--model'])
            emit(dict(event='progress',model_id=model['id'],path=model['component']+'/model.safetensors',
                completed_bytes=model['total_bytes']//3,total_bytes=model['total_bytes'],phase='downloading'))
            while (root/'hold-download').exists(): time.sleep(0.02)
            if (root/'fail-download').exists():
                emit(dict(event='error',message='下载中断，已保留进度；请检查网络或代理后继续。')); sys.exit(1)
            model.update(state='ready',installed_bytes=model['total_bytes'],verified_bytes=model['total_bytes'],resumable_bytes=0)
            models_file.write_text(json.dumps(models))
            emit(dict(event='verified'))
        elif command in ['select-model','move-models']:
            original.write_bytes(config_file.read_bytes())
            if command=='select-model':
                model = next(model for model in models if model['id']==options['--model'])
                config[model['component']+'_model_id']=model['id']
                config[model['component']+'_dir']=model['directory']
            else: config['models_dir']=options['--destination']
            config_file.write_text(json.dumps(config))
            emit(dict(event='managed'))
        elif command=='rollback-model-change':
            if original.exists(): config_file.write_bytes(original.read_bytes()); original.unlink()
            emit(dict(event='managed'))
        elif command=='commit-model-change':
            while (root/'hold-commit').exists(): time.sleep(0.02)
            if original.exists(): original.unlink()
            emit(dict(event='managed'))
        else: emit(dict(event='prepared' if command=='prepare' else 'verified'))
        """#
        try Data(script.utf8).write(to:helper)
        try fm.setAttributes([.posixPermissions:0o700],ofItemAtPath:helper.path)
        return helper
    }
}

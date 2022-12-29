const fsMod = require('fs')

exports.preCommit = (props) => {
    let versionContent = `VERSION = "${props.version}"`
    fsMod.writeFile('fritzexporter/_version.py', versionContent, (err) => {
        if (err) throw err;
    })
}

import json
import subprocess
import tempfile
import time

mongoExecCmds = '''
conf=/var/lib/juju/agents/machine-*/agent.conf
user=$(sudo grep '^tag:' ${conf} | cut -d ' ' -f2)
password=$(sudo awk '/statepassword/ {print $2}' ${conf})
client=/snap/bin/juju-db.mongo
certs=--tlsAllowInvalidCertificates
if sudo test -f /var/snap/juju-db/common/ca.crt; then
	certs="--tlsCertificateKeyFile=/var/snap/juju-db/common/server.pem --tlsCAFile=/var/snap/juju-db/common/ca.crt"
fi
# We use `--eval "$(cat exec-*.js)"` to get around snap confinement issues
sudo ${client} localhost:37017/juju --authenticationDatabase admin \
    --tls ${certs} \
    --username "${user}" --password "${password}" --eval "$(cat exec-*.js)"
'''


def mongoScript(opts): 
    script = '''

var toDelete = {delete};
s = db.getMongo().startSession();
var juju = s.getDatabase("juju");
var collections = juju.getCollectionNames().sort();
s.startTransaction()
var out = {{}};
var uuid = "{uuid}";

var model = juju.models.findOne({{ "_id": uuid }});
if (!model) {{
  throw "model " + uuid + " not found in models collection";
}}


collections.forEach(function (name) {{
    if (name.indexOf("system.") === 0) return;
    if (name.indexOf("txns.") === 0) return;

    var docs = juju
      .getCollection(name)
      .find({{"$or": [{{ "model-uuid": uuid }}, {{ "_id": {{"$regex": uuid}} }}]}})
      .toArray();

    if (docs.length > 0) {{
      out[name] = docs
      if (toDelete) {{
         juju
         .getCollection(name)
         .deleteMany({{"$or": [{{ "model-uuid": uuid }}, {{ "_id": {{"$regex": uuid}} }}]}});
      }}
    }}
  }});


  // Separetly record and handle globalRefcount and usermodelname documents

  var ref_count_id = "cloudModel#" + model.cloud;
  out["globalRefcounts"] = juju.globalRefcounts.find({{"_id": ref_count_id}}).toArray();

  var usermodelname_id = model.owner + ":" + model.name;
  out["usermodelname"] = juju.usermodelname.find({{ "_id": usermodelname_id }}).toArray();

  if (toDelete) {{
      // Decrement model cloud ref count
      juju.globalRefcounts.update({{"_id": ref_count_id}}, {{"$inc": {{"refcount": -1}}}})

      //Remove usermodelname entry so the user can recreate the model later using the same name
      juju.usermodelname.deleteOne({{ "_id": usermodelname_id }});
  }}


s.commitTransaction()
s.endSession()

print("----- start of content");
print(JSON.stringify(out));
'''
    return script.format(uuid=opts.model_uuid, delete="true" if opts.run else "false")

def strip_mongo_output(opts, content):
    """Turn the raw output from mongo into just the json tail"""
    start = "----- start of content\n"
    return content[content.index(start)+len(start):]


def exec_mongo_request(opts, mongo_request):
    local_file = tempfile.NamedTemporaryFile(delete_on_close=False, prefix="exec-", dir='.', suffix=".js")
    local_file.write(bytes(mongoScript(opts), 'utf-8'))
    local_file.flush()
    local_file.close()
    try:
        content = subprocess.check_output(["bash", "-c", mongo_request], text=True)
    except subprocess.CalledProcessError as e: 
        print(e.output)
        print(e.stderr)
        sys.exit(1)
    return strip_mongo_output(opts, content)

    
def main(args):
    import argparse
    p = argparse.ArgumentParser("simple script for removing model scoped documents")
    p.add_argument("--model-uuid", default="0", type=str, help="Juju model to completely cleanup.")
    p.add_argument("--run", action="store_true", help="Run the model document cleanup.")
    opts = p.parse_args(args)
    model_docs = exec_mongo_request(opts, mongoExecCmds)
    if (opts.run):
        file_name = "deleted-model-docs-" + opts.model_uuid + "-" + time.strftime("%Y%m%d-%H%M%S") + ".txt"
        print("Performed cleanup of model documents for " + opts.model_uuid)
        print("The list of deleted documents can found at " + file_name)
    else:
        file_name = "dry-run-model-docs-" + opts.model_uuid + "-" + time.strftime("%Y%m%d-%H%M%S") + ".txt"
        print("Performed dry-run of model documents for " + opts.model_uuid)
        print("The list of documents that would be deleted can found at " + file_name)
    with open(file_name, "w") as f:
      f.write(model_docs)
            

if __name__ == "__main__":
    import sys
    main(sys.argv[1:])
 
import requests
from requests.exceptions import HTTPError
import attr
import yaml
import click
import os
from pathlib import Path
import difflib
import pprint
import json


META_FILE = '.meta.yaml'
SERIALIZER = 'serializer'

READ_ONLY = 'read_only'
def modifiable(field, value):
    return not field.metadata.get(READ_ONLY, False) and value is not None


@attr.s
class LazyCollection():
    optimizely = attr.ib()
    cls = attr.ib()
    endpoint = attr.ib()
    params = attr.ib(default=None)
    _items = attr.ib(default=None, init=False)

    def items(self):
        if self._items is None:
            self._items = {}
            params={
                'per_page': 100,
            }
            if self.params is not None:
                params.update(self.params)
            response = self.optimizely.session.get(
                'https://api.optimizely.com/v2/{}'.format(self.endpoint),
                params=params,
            )

            for doc in response.json():
                obj = self.cls(**doc)
                self._items[obj.id] = obj
        return self._items.items()

    def values(self):
        for _, value in self.items():
            yield value

    def __iter__(self):
        for key, _ in self.items():
            yield key

    def __getitem__(self, key):
        if self._items is not None and key in self._items:
            return self._items[key]

        response = self.optimizely.session.get(
            'https://api.optimizely.com/v2/{}/{}'.format(self.endpoint, key),
        )
        self.optimizely.raise_for_status(response)
        return self.cls(**response.json())

    def __setitem__(self, key, value):
        changes = attr.asdict(value, filter=modifiable)
        response = self.optimizely.session.patch(
            'https://api.optimizely.com/v2/{}/{}'.format(self.endpoint, key),
            json=changes,
        )
        self.optimizely.raise_for_status(response)


class Optimizely():
    def __init__(self, token):
        self.session = requests.Session()
        self.session.headers['Authorization'] = "Bearer {}".format(token)

    def raise_for_status(self, response):
        if not response.ok:
            raise HTTPError(response.json().get('message', response.reason), response=response)

    @property
    def projects(self):
        return LazyCollection(self, Project, 'projects')

    def experiments(self, project_id=None):
        if project_id:
            params = {
                'project_id': project_id,
            }
        else:
            params = None
        return LazyCollection(self, Experiment, 'experiments', params)

    def audiences(self, project_id=None):
        if project_id:
            params = {
                'project_id': project_id,
            }
        else:
            params = None
        return LazyCollection(self, Audience, 'audiences', params)

    def pages(self, project_id=None):
        if project_id:
            params = {
                'project_id': project_id,
            }
        else:
            params = None
        return LazyCollection(self, Page, 'pages', params)



COLLECTION_CLS = 'collection_cls'

def subdocuments(cls, metadata=None):
    _metadata = {
        COLLECTION_CLS: cls,
    }
    if metadata is not None:
        _metadata.update(metadata)

    return attr.ib(
        convert=lambda docs: [cls(**doc) for doc in docs],
        metadata=_metadata,
    )


class OptimizelyDocument():
    @classmethod
    def read_from_disk(cls, root):
        meta = read_meta_file(root)

        for field in attr.fields(cls):
            if COLLECTION_CLS in field.metadata:
                docs = []
                subdir = root / field.name
                if subdir.exists():
                    for dirname in meta.get(field.name):
                        docdir = subdir / dirname
                        if not docdir.is_dir():
                            continue
                        change = field.metadata[COLLECTION_CLS].read_from_disk(docdir)
                        docs.append(as_non_null_dict(change))
                meta[field.name] = docs

        obj = cls(**meta)

        for field in attr.fields(cls):
            if SERIALIZER in field.metadata:
                serializer = field.metadata[SERIALIZER](root, obj, field.name)
                setattr(obj, field.name, serializer.read_from_disk())

        return obj

    def write_to_disk(self, root):
        docroot = root / self.dirname
        docroot.mkdir(parents=True, exist_ok=True)

        meta = as_non_null_dict(self)

        for field in attr.fields(self.__class__):
            if COLLECTION_CLS in field.metadata:
                objs = []
                for obj in getattr(self, field.name):
                    obj.write_to_disk(docroot / field.name)
                    objs.append(obj.dirname)
                meta[field.name] = objs
            elif SERIALIZER in field.metadata:
                serializer = field.metadata[SERIALIZER](docroot, self, field.name)
                serializer.write_to_disk()
                meta.pop(field.name, None)

        write_meta_file(docroot, meta)

    @property
    def dirname(self):
        return slugify("{} {}".format(self.name, self.id))


@attr.s
class ConditionSerializer():
    root = attr.ib()
    obj = attr.ib()
    fieldname = attr.ib()

    @property
    def filename(self):
        return self.root / '{}.json'.format(self.fieldname)

    def read_from_disk(self):
        with self.filename.open() as field_file:
            return json.dumps(json.load(field_file))

    def write_to_disk(self):
        data = getattr(self.obj, self.fieldname)
        if data is not None:
            with self.filename.open('w') as field_file:
                json.dump(
                    json.loads(data),
                    fp=field_file,
                    indent=2,
                )


@attr.s
class StaticContentSerializer():
    root = attr.ib()
    obj = attr.ib()
    fieldname = attr.ib()

    @property
    def filename(self):
        if self.obj.type == 'custom_css':
            extension = 'css'
        elif self.obj.type == 'custom_code':
            extension = 'js'
        elif self.obj.type in ('insert_html', 'insert_image'):
            extension = 'html'
        else:
            extension = 'txt'
        return self.root / '{}.{}'.format(self.fieldname, extension)

    def read_from_disk(self):
        with self.filename.open() as field_file:
            return field_file.read()

    def write_to_disk(self):
        data = getattr(self.obj, self.fieldname)
        if data is not None:
            with self.filename.open('w') as field_file:
                field_file.write(data)

@attr.s
class Project(OptimizelyDocument):
    name = attr.ib()
    confidence_threshold = attr.ib()
    platform = attr.ib()
    sdks = attr.ib()
    status = attr.ib()
    web_snippet = attr.ib()
    account_id = attr.ib(metadata={READ_ONLY: True})
    created = attr.ib(metadata={READ_ONLY: True})
    id = attr.ib()
    is_classic = attr.ib(metadata={READ_ONLY: True})
    last_modified = attr.ib(metadata={READ_ONLY: True})
    socket_token = attr.ib(default=None, metadata={READ_ONLY: True})
    dcp_service_id = attr.ib(default=None)


@attr.s
class Audience(OptimizelyDocument):
    project_id = attr.ib(metadata={READ_ONLY: True})
    archived = attr.ib()
    conditions = attr.ib(metadata={SERIALIZER: ConditionSerializer})
    description = attr.ib()
    is_classic = attr.ib(metadata={READ_ONLY: True})
    name = attr.ib()
    segmentation = attr.ib()
    created = attr.ib(metadata={READ_ONLY: True})
    id = attr.ib()
    last_modified = attr.ib(metadata={READ_ONLY: True})


@attr.s
class Page(OptimizelyDocument):
    edit_url = attr.ib()
    name = attr.ib()
    project_id = attr.ib(metadata={READ_ONLY: True})
    archived = attr.ib()
    category = attr.ib()
    conditions = attr.ib(metadata={SERIALIZER: ConditionSerializer})
    key = attr.ib()
    page_type = attr.ib()
    created = attr.ib(metadata={READ_ONLY: True})
    id = attr.ib()
    last_modified = attr.ib(metadata={READ_ONLY: True})
    activation_code = attr.ib(default=None)
    activation_type = attr.ib(default=None)


@attr.s
class Change(OptimizelyDocument):
    dependencies = attr.ib()
    id = attr.ib()
    type = attr.ib()
    async = attr.ib(default=None)
    allow_additional_redirect = attr.ib(default=None)
    attributes = attr.ib(default=None)
    config = attr.ib(default=None)
    css = attr.ib(default=None)
    destination = attr.ib(default=None)
    extension_id = attr.ib(default=None, metadata={READ_ONLY: True})
    name = attr.ib(default=None)
    operator = attr.ib(default=None)
    preserve_parameters = attr.ib(default=None)
    rearrange = attr.ib(default=None)
    selector = attr.ib(default=None)
    src = attr.ib(default=None, metadata={READ_ONLY: True})
    value = attr.ib(default=None, metadata={SERIALIZER: StaticContentSerializer})

    @property
    def dirname(self):
        return slugify(self.id)


@attr.s
class Action(OptimizelyDocument):
    changes = subdocuments(Change)
    page_id = attr.ib()

    @property
    def dirname(self):
        return slugify(str(self.page_id))



@attr.s
class Variation(OptimizelyDocument):
    weight = attr.ib()
    actions = subdocuments(Action)
    archived = attr.ib()
    variation_id = attr.ib()
    key = attr.ib(default=None)
    name = attr.ib(default=None)

    @property
    def dirname(self):
        return slugify("{} {}".format(self.name, self.variation_id))


@attr.s
class Experiment(OptimizelyDocument):
    changes = subdocuments(Change)
    created = attr.ib(metadata={READ_ONLY: True})
    id = attr.ib()
    is_classic = attr.ib(metadata={READ_ONLY: True})
    last_modified = attr.ib(metadata={READ_ONLY: True})
    metrics = attr.ib()
    project_id = attr.ib()
    status = attr.ib(metadata={READ_ONLY: True})
    type = attr.ib()
    variations = subdocuments(Variation)
    audience_conditions = attr.ib(default=None, metadata={SERIALIZER: ConditionSerializer})
    campaign_id = attr.ib(default=None)
    description = attr.ib(default=None)
    earliest = attr.ib(default=None, metadata={READ_ONLY: True})
    holdback = attr.ib(default=None)
    key = attr.ib(default=None)
    latest = attr.ib(default=None, metadata={READ_ONLY: True})
    name = attr.ib(default=None)
    schedule = attr.ib(default=None)


def write_meta_file(root, meta_document):
    meta_file = root / META_FILE

    with meta_file.open('w') as meta_file_handle:
        yaml.safe_dump(
            {k: v for k, v in meta_document.items() if v is not None},
            stream=meta_file_handle,
            default_flow_style=False,
        )

def read_meta_file(root):
    meta_file = root / META_FILE

    with meta_file.open() as meta_file_handle:
        return yaml.safe_load(meta_file_handle)


def slugify(directory):
    return directory.replace(' ', '_')


def as_non_null_dict(obj):
    return attr.asdict(obj, filter=lambda a, v: v is not None)


@click.group()
@click.password_option('--token', envvar='OPTIMIZELY_TOKEN')
@click.pass_context
def cli(ctx, token):
    if ctx.obj is None:
        ctx.obj = {}

    ctx.obj['OPTIMIZELY'] = Optimizely(token)

@cli.command()
@click.option('--root', default='.', type=click.Path(exists=True, file_okay=False))
@click.pass_context
def pull(ctx, root):
    optimizely = ctx.obj['OPTIMIZELY']
    project_root = Path(root)
    for project in optimizely.projects.values():
        project.write_to_disk(project_root)

        for object_type in ('experiments', 'audiences', 'pages'):
            obj_root = project_root / project.dirname / object_type
            for obj in getattr(optimizely, object_type)(project.id).values():
                obj.write_to_disk(obj_root)


@cli.command('pull-experiment')
@click.argument('experiment', type=click.Path(exists=True, file_okay=False))
@click.pass_context
def pull_experiment(ctx, experiment):
    optimizely = ctx.obj['OPTIMIZELY']

    local = Experiment.read_from_disk(Path(experiment))
    remote = optimizely.experiments()[local.id]

    remote.write_to_disk(Path(experiment).parent)


@cli.command('push-experiment')
@click.argument('experiment', type=click.Path(exists=True, file_okay=False))
@click.option('--context-lines', '-n', type=int, default=3)
@click.pass_context
def push_experiment(ctx, experiment, context_lines):
    optimizely = ctx.obj['OPTIMIZELY']

    local = Experiment.read_from_disk(Path(experiment))
    remote = optimizely.experiments()[local.id]

    remote_doc = attr.asdict(remote, filter=modifiable)
    local_doc = attr.asdict(local, filter=modifiable)

    if local_doc == remote_doc:
        click.secho("No changes!", fg='green')
    else:
        for diffline in difflib.unified_diff(
            yaml.dump(remote_doc, default_flow_style=False).splitlines(),
            yaml.dump(local_doc, default_flow_style=False).splitlines(),
            fromfile='remote',
            tofile='local',
            n=context_lines,
        ):
            if diffline.startswith(' '):
                click.secho(diffline, fg='white')
            elif diffline.startswith('-'):
                click.secho(diffline, fg='red')
            elif diffline.startswith('+'):
                click.secho(diffline, fg='green')
            elif diffline.startswith('?'):
                click.secho(diffline, fg='yellow')

        if click.confirm('Push these experiment changes?'):
            optimizely.experiments()[local.id] = local


if __name__ == '__main__':
    cli()

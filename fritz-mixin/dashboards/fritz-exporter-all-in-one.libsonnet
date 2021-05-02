local grafana = import 'grafonnet-7.0/grafana.libsonnet';
local dashboard = grafana.dashboard;
local row = grafana.panel.row;
local prometheus = grafana.target.prometheus;
local stat = grafana.panel.stat;
local graph = grafana.panel.graph;

{
  grafanaDashboards+:: {
    'fritz-exporter-all-in-one.json':
      dashboard.new(
        title='Fritz!Exporter',
        description='Demo Dashboard for stats from fritz_exporter (https://github.com/pdreker/fritzbox_exporter)',
        editable=true,
      )
      .setTime(from="now-1h")
      .addTemplate(
        {
          current: {
            text: 'Prometheus',
            value: 'Prometheus',
          },
          hide: 0,
          label: null,
          name: 'datasource',
          options: [],
          query: 'prometheus',
          refresh: 1,
          regex: '',
          type: 'datasource',
        },
      )
      .addTemplate(
        {
          hide: 0,
          label: 'Device',
          name: 'device',
          query: 'label_values(fritz_update_available, friendly_name)',
          refresh: 2,
          type: 'query',
        }
      )
      .addTemplate(
        {
          hide: 0,
          label: 'WiFi Network',
          name: 'wifi',
          query: 'label_values(fritz_wifi_status, wifi_name)',
          refresh: 2,
          type: 'query',
        }
      )


      ###
      # General row
      ###
      .addPanel(
        row.new(title='General', collapsed=false)
        .setGridPos(x=0, y=0)
      )

      # DSL Link Status
      .addPanel(
        stat.new(
          title='DSL Link Status',
          datasource='$datasource',
        )
        .setGridPos(h=5, w=2, x=0)
        .setOptions(graphMode='area', calcs=['lastNotNull'])
        .addMapping(value="0", text="Offline", type=1)
        .addMapping(value="1", text="Online", type=1)
        .addThresholdStep(color="red", value=null)
        .addThresholdStep(color="green", value=0.1)
        .addTarget(prometheus.new(datasource='$datasource',
          expr='fritz_wan_phys_link_status{friendly_name="$device"}',
        ))
      )

      # DSL Status
      .addPanel(
        stat.new(
          title='DSL Status',
          datasource='$datasource',
        )
        .setGridPos(h=5, w=2, x=2)
        .setOptions(graphMode='area', calcs=['lastNotNull'])
        .addMapping(value="0", text="Offline", type=1)
        .addMapping(value="1", text="Online", type=1)
        .addThresholdStep(color="red", value=null)
        .addThresholdStep(color="green", value=0.1)
        .addTarget(prometheus.new(datasource='$datasource',
          expr='fritz_dsl_status{friendly_name="$device"}',
        ))
      )

      # PPP Status
      .addPanel(
        stat.new(
          title='PPP Status',
          datasource='$datasource',
        )
        .setGridPos(h=5, w=2, x=4)
        .setOptions(graphMode='area', calcs=['lastNotNull'])
        .addMapping(value="0", text="Offline", type=1)
        .addMapping(value="1", text="Online", type=1)
        .addThresholdStep(color="red", value=null)
        .addThresholdStep(color="green", value=0.1)
        .addTarget(prometheus.new(datasource='$datasource',
          expr='fritz_ppp_connection_state{friendly_name="$device"}',
        ))
      )

      # PPP Uptime
      .addPanel(
        stat.new(
          title='PPP Uptime',
          datasource='$datasource',
        )
        .setGridPos(h=5, w=3, x=6)
        .setOptions(graphMode='none', calcs=['lastNotNull'])
        .addThresholdStep(color="green", value=null)
        .setFieldConfig(unit='dtdurations')
        .addTarget(prometheus.new(datasource='$datasource',
          expr='fritz_ppp_connection_uptime_seconds_total{friendly_name="$device"}',
        ))
      )

      # DSL Enabled
      .addPanel(
        stat.new(
          title='DSL Enabled',
          datasource='$datasource',
        )
        .setGridPos(h=5, w=2, x=9)
        .setOptions(graphMode='none', calcs=['lastNotNull'])
        .addMapping(value="0", text="Disabled", type=1)
        .addMapping(value="1", text="Enabled", type=1)
        .addThresholdStep(color="red", value=null)
        .addThresholdStep(color="green", value=0.1)
        .addTarget(prometheus.new(datasource='$datasource',
          expr='fritz_dsl_status_enabled{friendly_name="$device"}',
        ))
      )

      # LAN Status
      .addPanel(
        stat.new(
          title='LAN Status',
          datasource='$datasource',
        )
        .setGridPos(h=5, w=2, x=11)
        .setOptions(graphMode='none', calcs=['lastNotNull'])
        .addMapping(value="0", text="Offline", type=1)
        .addMapping(value="1", text="Online", type=1)
        .addThresholdStep(color="red", value=null)
        .addThresholdStep(color="green", value=0.1)
        .addTarget(prometheus.new(datasource='$datasource',
          expr='fritz_lan_status{friendly_name="$device"}',
        ))
      )

      # LAN Enabled
      .addPanel(
        stat.new(
          title='LAN Enabled',
          datasource='$datasource',
        )
        .setGridPos(h=5, w=2, x=13)
        .setOptions(graphMode='none', calcs=['lastNotNull'])
        .addMapping(value="0", text="Disabled", type=1)
        .addMapping(value="1", text="Enabled", type=1)
        .addThresholdStep(color="red", value=null)
        .addThresholdStep(color="green", value=0.1)
        .addTarget(prometheus.new(datasource='$datasource',
          expr='fritz_lan_status_enabled{friendly_name=~"$device"}',
        ))
      )

      # Update available
      .addPanel(
        stat.new(
          title='Update available',
          datasource='$datasource',
        )
        .setGridPos(h=5, w=2, x=15)
        .setOptions(graphMode='none', calcs=['lastNotNull'])
        .addMapping(value="0", text="No", type=1)
        .addMapping(value="1", text="Yes", type=1)
        .addThresholdStep(color="green", value=null)
        .addThresholdStep(color="red", value=0.1)
        .addTarget(prometheus.new(datasource='$datasource',
          expr='fritz_update_available{friendly_name="$device"}',
        ))
      )

      # Known devices
      .addPanel(
        stat.new(
          title='Known Devices',
          datasource='$datasource',
        )
        .setGridPos(h=5, w=3, x=17)
        .setOptions(calcs=['lastNotNull'], graphMode='area')
        .addTarget(prometheus.new(datasource='$datasource',
          expr='fritz_known_devices_count{friendly_name="$device"}',
        ))
      )

      # Uptime
      .addPanel(
        stat.new(
          title='Uptime',
          datasource='$datasource',
        )
        .setGridPos(h=5, w=4, x=20)
        .setFieldConfig(unit='dtdurations')
        .addThresholdStep(color="green", value=null)
        .setOptions(graphMode='none', calcs=['lastNotNull'])
        .addTarget(prometheus.new(datasource='$datasource',
          expr='min(fritz_uptime_seconds_total{friendly_name="$device"}) without(softwareversion)',
        ))
      )

      ###
      # LAN row
      ###
      .addPanel(
        row.new(title='LAN', collapsed=false)
        .setGridPos(x=0, y=6)
      )

      # LAN bytes/s
      .addPanel(
        graph.new(
          title='LAN bytes/s',
          datasource='$datasource'
        )
        .setGridPos(h=8, w=12, x=0, y=7)
        .setFieldConfig(unit='binBps', min=0)
        .addTarget(prometheus.new(
          datasource='$datasource',
          expr='irate(fritz_lan_data_bytes_total{friendly_name="$device"}[10m])',
          legendFormat='{{ direction }}'
        ))
      )

      # LAN packets/s
      .addPanel(
        graph.new(
          title='LAN packet/s',
          datasource='$datasource'
        )
        .setGridPos(h=8, w=12, x=12, y=7)
        .setFieldConfig(min=0)
        .addTarget(prometheus.new(
          datasource='$datasource',
          expr='irate(fritz_lan_packet_count_total{friendly_name="$device"}[10m])',
          legendFormat='{{ direction }}'
        ))
      )

      ###
      # WiFi row
      ###
      .addPanel(
        row.new(title='Wifi', collapsed=false)
        .setGridPos(x=0, y=15)
      )

      # WiFi Enabled
      .addPanel(
        stat.new(
          title='Wifi Enabled',
          datasource='$datasource',
        )
        .setGridPos(h=5, w=2, x=0, y=16)
        .setOptions(graphMode='none', calcs=['lastNotNull'], textMode='name')
        .addMapping(value="0", text="Disabled", type=1)
        .addMapping(value="1", text="Enabled", type=1)
        .addThresholdStep(color="red", value=null)
        .addThresholdStep(color="green", value=0.1)
        .addTarget(prometheus.new(datasource='$datasource',
          expr='fritz_wifi_status{friendly_name=~"$device", wifi_name="$wifi"}',
          legendFormat='{{ enabled }}'
        ))
      )

      # WiFi SSID
      .addPanel(
        stat.new(
          title='SSID',
          datasource='$datasource',
        )
        .setGridPos(h=5, w=2, x=2, y=16)
        .setOptions(graphMode='none', calcs=['lastNotNull'], textMode='name')
        .addThresholdStep(color="green", value=null)
        .addTarget(prometheus.new(datasource='$datasource',
          expr='fritz_wifi_status{friendly_name=~"$device", wifi_name="$wifi"}',
          legendFormat='{{ ssid }}',
        ))
      )

      # WiFi Channel
      .addPanel(
        stat.new(
          title='Channel',
          datasource='$datasource',
        )
        .setGridPos(h=5, w=2, x=4, y=16)
        .setOptions(graphMode='none', calcs=['lastNotNull'])
        .addThresholdStep(color="green", value=null)
        .addTarget(prometheus.new(datasource='$datasource',
          expr='fritz_wifi_channel{friendly_name=~"$device", wifi_name="$wifi"}',
        ))
      )

      # WiFi Status
      .addPanel(
        stat.new(
          title='Status',
          datasource='$datasource',
        )
        .setGridPos(h=5, w=2, x=0, y=21)
        .setOptions(graphMode='none', calcs=['lastNotNull'])
        .addMapping(value="0", text="Off", type=1)
        .addMapping(value="1", text="On", type=1)
        .addThresholdStep(color="red", value=null)
        .addThresholdStep(color="green", value=0.1)
        .addTarget(prometheus.new(datasource='$datasource',
          expr='fritz_wifi_status{friendly_name=~"$device", wifi_name="$wifi"}',
        ))
      )

      # WiFi Current Associations
      .addPanel(
        stat.new(
          title='Associations',
          datasource='$datasource',
        )
        .setGridPos(h=5, w=2, x=2, y=21)
        .setOptions(graphMode='area', calcs=['lastNotNull'])
        .addTarget(prometheus.new(datasource='$datasource',
          expr='fritz_wifi_associations_count{friendly_name=~"$device", wifi_name="$wifi"}',
        ))
      )

      # WiFi standard
      .addPanel(
        stat.new(
          title='Wifi standard',
          datasource='$datasource',
        )
        .setGridPos(h=5, w=2, x=4, y=21)
        .setOptions(graphMode='none', calcs=['lastNotNull'], textMode='name')
        .addTarget(prometheus.new(datasource='$datasource',
          expr='fritz_wifi_status{friendly_name=~"$device", wifi_name="$wifi"}',
          legendFormat='802.11{{ standard }}',
        ))
      )

      # Packets/s
      .addPanel(
        graph.new(
          title='Packets/s',
          datasource='$datasource',
        )
        .setGridPos(h=10, w=9, x=6, y=16)
        .setFieldConfig(min=0)
        .addTarget(prometheus.new(datasource='$datasource',
          expr='irate(fritz_wifi_packets_count_total{friendly_name="$device", wifi_name="$wifi"}[10m])',
          legendFormat='{{ direction }}',
        ))
      )

      # Associations
      .addPanel(
        graph.new(
          title='Associations',
          datasource='$datasource',
          bars=true,
        )
        .setGridPos(h=10, w=9, x=15, y=16)
        .setFieldConfig(min=0)
        .addTarget(prometheus.new(datasource='$datasource',
          expr='fritz_wifi_associations_count{friendly_name="$device", wifi_name="$wifi"}',
          legendFormat='associations',
        ))
      )

      ###
      # WAN row
      ###
      .addPanel(
        row.new(title='WAN', collapsed=false)
        .setGridPos(x=0, y=26)
      )

      # WAN bytes/s
      .addPanel(
        graph.new(
          title='WAN bytes/s',
          datasource='$datasource'
        )
        .setGridPos(h=8, w=12, x=0, y=27)
        .setFieldConfig(unit='binBps', min=0)
        .addTarget(prometheus.new(
          datasource='$datasource',
          expr='irate(fritz_wan_data_bytes_total{friendly_name="$device"}[10m])',
          legendFormat='{{ direction }}'
        ))
      )

      # WAN packets/s
      .addPanel(
        graph.new(
          title='WAN packet/s',
          datasource='$datasource'
        )
        .setGridPos(h=8, w=12, x=12, y=27)
        .setFieldConfig(min=0)
        .addTarget(prometheus.new(
          datasource='$datasource',
          expr='irate(fritz_wan_data_packets_count_total{friendly_name="$device"}[10m])',
          legendFormat='{{ direction }}'
        ))
      )

      ###
      # DSL row
      ###
      .addPanel(
        row.new(title='DSL Link', collapsed=false)
        .setGridPos(x=0, y=35)
      )

      # DSL current Linkrate
      .addPanel(
        stat.new(
          title='Current linkrate',
          datasource='$datasource',
        )
        .setGridPos(h=6, w=8, x=0, y=36)
        .setOptions(graphMode='area', calcs=['lastNotNull'])
        .setFieldConfig(unit='Kbits')
        .addThresholdStep(color="green", value=null)
        .addTarget(prometheus.new(datasource='$datasource',
          expr='fritz_dsl_datarate_kbps{type="curr", friendly_name="$device"}',
          legendFormat='{{ direction }}',
        ))
      )

      # DSL max Linkrate
      .addPanel(
        stat.new(
          title='Max linkrate',
          datasource='$datasource',
        )
        .setGridPos(h=6, w=8, x=0, y=42)
        .setOptions(graphMode='none', calcs=['lastNotNull'])
        .setFieldConfig(unit='Kbits')
        .addThresholdStep(color="green", value=null)
        .addTarget(prometheus.new(datasource='$datasource',
          expr='fritz_dsl_datarate_kbps{type="max", friendly_name="$device"}',
          legendFormat='{{ direction }}',
        ))
      )

      # DSL attenuation
      .addPanel(
        stat.new(
          title='DSL attenuation',
          datasource='$datasource',
        )
        .setGridPos(h=6, w=8, x=8, y=36)
        .setOptions(graphMode='area', calcs=['lastNotNull'])
        .setFieldConfig(unit='dB')
        .addThresholdStep(color="green", value=null)
        .addTarget(prometheus.new(datasource='$datasource',
          expr='fritz_dsl_attenuation_dB{friendly_name="$device"}',
          legendFormat='{{ direction }}',
        ))
      )

      # DSL noise margin
      .addPanel(
        stat.new(
          title='DSL noise margin',
          datasource='$datasource',
        )
        .setGridPos(h=6, w=8, x=8, y=42)
        .setOptions(graphMode='area', calcs=['lastNotNull'])
        .setFieldConfig(unit='dB')
        .addThresholdStep(color="green", value=null)
        .addTarget(prometheus.new(datasource='$datasource',
          expr='fritz_dsl_noise_margin_dB{friendly_name="$device"}',
          legendFormat='{{ direction }}',
        ))
      )

      .addPanel(
        graph.new(
          title='DSL Error Rates',
          datasource='$datasource',
        )
        .setGridPos(h=12, w=8, x=16, y=36)
        .addTarget(prometheus.new(datasource='$datasource',
          expr='rate(fritz_dsl_crc_errors_count_total{friendly_name="$device"}[10m])',
          legendFormat='FEC',
        ))
        .addTarget(prometheus.new(datasource='$datasource',
          expr='rate(fritz_dsl_fec_errors_count_total{friendly_name="$device"}[10m])',
          legendFormat='CRC',
        ))

      )

  },
}
